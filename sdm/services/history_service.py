"""耗電與電量歷史的檔案服務：載入、每 15 筆存檔、結束時存檔。

保護規則：
- 檔案完全無法解讀時，先把原始位元組複製成新的 history-<epoch>.json.bak
  （同名已存在就加 -1、-2…，絕不覆寫），原檔留到下次存檔才被取代。
- 無法讀取、無法備份或來自較新版本時，本次執行停用寫入，原檔不動。
- 檔案超過 20 MB 時不讀取，比照無法解讀的檔案處理（分段複製成備份）。
- 個別無效的列（非有限值、超出範圍）在載入時捨棄；時間在未來的列保留。
- 時鐘落後（現在早於最新一筆）時不修剪；修剪若會刪到既有最新一筆前 24 小時內的
  資料（時鐘大幅往前跳），本次執行第一次發生時先另存備份再修剪。
- 由 UPower 補回的紀錄（imported）與自己的紀錄分開保存在同一個檔案的 "imported" 欄位，
  加入時立即存檔；這個欄位無效時只捨棄它（之後會再補一次），自己的紀錄照常載入。
  載入時只留落在自己紀錄空白裡面的補回列（手改或過時的檔案不會讓兩種來源交錯）。
"""
import logging
import os
from pathlib import Path
import time

from sdm import paths
from sdm.constants import History
from sdm.core.backfill import (Imported, InvalidImported, decode_imported, imported_section,
                               keep_in_gaps, merge_imported, trim_imported)
from sdm.core.history import (NewerFormat, Undecodable, chart_points, decode_rows,
                              encode_history, last_unplugged, load_document, parse_boot_time,
                              trim, valid_sample)
from sdm.core.jsonfile import (create_backup, create_backup_copy, remove_stale_temporaries,
                               write_bytes_atomic)
from sdm.data.sources import read

LOG = logging.getLogger(__name__)
PROC_STAT = "/proc/stat"


def default_path():
    return paths.DATA / History.FILE_NAME


def read_boot_time(path=PROC_STAT):
    """這次開機的 Unix 時間；讀不到時回傳 None。"""
    text = read(path)
    return parse_boot_time(text) if text else None


class HistoryService:
    """在主執行緒使用；samples 是不可變的 tuple，每次新增都換成新的 tuple。"""

    def __init__(self, path=None, clock=time.time, writer=write_bytes_atomic,
                 backup=create_backup, memory=False, monotonic=time.monotonic,
                 copy_backup=create_backup_copy):
        self.memory, self.monotonic, self.copy_backup = memory, monotonic, copy_backup
        if memory:
            self.path = None
        else:
            self.path = Path(path) if path is not None else default_path()
        self.clock, self.writer, self.backup = clock, writer, backup
        self.samples = ()
        self.imported = Imported()     # 由 UPower 補回的紀錄與處理過的空白（不可變）
        self.unsaved = 0
        self.writable = True
        self.jump_backup_done = False
        self.failures, self.retry_at, self.gave_up = 0, None, False

    @classmethod
    def in_memory(cls, **options):
        """模擬測試用：只存在記憶體，不讀取也不寫入任何檔案。"""
        return cls(memory=True, **options)

    @classmethod
    def preloaded(cls, samples, **options):
        """示範模式用：只在記憶體，先放入示範紀錄（修剪到 7 天，依時間排序）。"""
        service = cls(memory=True, **options)
        service.samples = trim(tuple(sorted(samples, key=lambda item: item.t)), service.clock())
        return service

    def load(self):
        if self.memory:
            return
        remove_stale_temporaries(self.path)
        try:
            size = self.path.stat().st_size
            if size > History.MAX_FILE_BYTES:
                self._recover_oversized()
                return
            data = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            self._disable("History file unreadable (%s)", type(error).__name__)
            return
        self._decode(data, self.clock())

    def _decode(self, data, now):
        try:
            raw = load_document(data)
        except NewerFormat:
            self._disable("History file uses a newer format")
            return
        except Undecodable:
            self._recover(data, now)
            return
        samples, dropped = decode_rows(raw["samples"])
        if dropped:
            LOG.warning("Dropped %d invalid history rows", dropped)
        newest = samples[-1].t if samples else None
        self.samples = self._trim(samples, newest, now, data)
        self.imported = self._decode_imported(raw.get("imported"), now)

    def _decode_imported(self, raw, now):
        """補回的欄位無效時整個捨棄（記警告），自己的紀錄不受影響；不在空白裡的補回列也丟掉。"""
        try:
            imported = trim_imported(decode_imported(raw), now)
        except InvalidImported:
            LOG.warning("Dropped the invalid imported history section")
            return Imported()
        imported, dropped = keep_in_gaps(imported, self.samples, now)
        if dropped:
            LOG.warning("Dropped %d imported history rows next to own samples", dropped)
        return imported

    def _recover_oversized(self):
        """太大的檔案不讀進記憶體：分段複製成備份後從空白開始；備份失敗就停用寫入。"""
        try:
            backup = self.copy_backup(self.path, int(self.clock()), mode=History.FILE_MODE)
        except OSError as error:
            self._disable("History file is too large and could not be backed up (%s)",
                          type(error).__name__)
            return
        LOG.warning("History file exceeds %d bytes; copied to %s and starting empty",
                    History.MAX_FILE_BYTES, backup.name)

    def _disable(self, message, *args):
        self.writable = False
        LOG.warning(message + "; history saving disabled for this session", *args)

    def _recover(self, data, now):
        try:
            backup = self.backup(self.path, data, int(now), mode=History.FILE_MODE)
        except OSError as error:
            self._disable("History file is invalid and could not be backed up (%s)",
                          type(error).__name__)
            return
        LOG.warning("History file is invalid; copied to %s and starting empty", backup.name)

    def add(self, sample):
        """加入一筆並修剪到 7 天；累積 15 筆時存檔。回傳這次是否寫檔成功。"""
        now = self.clock()
        if not valid_sample(sample, now):
            LOG.debug("Ignoring an invalid history sample")
            return False
        samples = self.samples + (sample,)
        if self.samples and sample.t < self.samples[-1].t:
            samples = tuple(sorted(samples, key=lambda item: item.t))
        newest = self.samples[-1].t if self.samples else None
        self.samples = self._trim(samples, newest, now)
        self.unsaved += 1
        return self.unsaved >= History.SAVE_EVERY and self.save()

    def add_imported(self, series, gaps, samples):
        """加入由 UPower 補回的樣本，並記下這些空白已處理；立即存檔，回傳這次是否寫檔成功。"""
        self.imported = merge_imported(self.imported, series, gaps, samples, self.clock())
        self.unsaved += 1
        return self.save()

    def _trim(self, samples, newest_before, now, original=None):
        """修剪到 7 天；會刪到既有最新一筆前 24 小時內的資料時（時鐘往前跳）先備份。"""
        kept = trim(samples, now)
        if len(kept) == len(samples) or newest_before is None:
            return kept
        window = newest_before - History.CLOCK_JUMP_WINDOW_SECONDS
        cutoff = now - History.RETENTION_SECONDS
        if not any(window <= item.t < cutoff for item in samples):
            return kept
        data = original if original is not None else encode_history(samples)
        return kept if self._backup_before_jump(data, now) else tuple(samples)

    def _backup_before_jump(self, data, now):
        """每次執行只備份一次；備份失敗時不修剪，並停用寫入，原檔保持不變。"""
        if self.jump_backup_done or self.memory:
            return True
        try:
            backup = self.backup(self.path, data, int(now), mode=History.FILE_MODE)
        except OSError as error:
            self._disable("Clock jumped forward and history could not be backed up (%s)",
                          type(error).__name__)
            return False
        self.jump_backup_done = True
        LOG.warning("Clock jumped forward; copied history to %s before trimming", backup.name)
        return True

    def save(self, force=False):
        """有未存的紀錄時原子寫檔；失敗時保留紀錄，依退避時間再試。

        force 為 True（結束時）會忽略退避等待，但連續失敗 5 次放棄後就不再嘗試。
        """
        if self.memory or not self.writable or self.gave_up or not self.unsaved:
            return False
        if not force and self.retry_at is not None and self.monotonic() < self.retry_at:
            return False
        self.imported = trim_imported(self.imported, self.clock())
        try:
            data = encode_history(self.samples, imported_section(self.imported))
            self._ensure_directory()
            self.writer(self.path, data, mode=History.FILE_MODE)
        except (OSError, ValueError) as error:
            self._save_failed(type(error).__name__)
            return False
        self.unsaved, self.failures, self.retry_at = 0, 0, None
        return True

    def _save_failed(self, name):
        """第一次失敗記警告，之後只記除錯訊息；連續失敗達上限時記最後一次警告並放棄。"""
        self.failures += 1
        if self.failures >= History.SAVE_MAX_FAILURES:
            self.gave_up = True
            LOG.warning("Cannot save history (%s); giving up for this session after %d attempts",
                        name, self.failures)
            return
        delays = History.SAVE_RETRY_SECONDS
        self.retry_at = self.monotonic() + delays[min(self.failures, len(delays)) - 1]
        LOG.log(logging.WARNING if self.failures == 1 else logging.DEBUG,
                "Cannot save history (%s); will retry later", name)

    def _ensure_directory(self):
        """資料夾不存在時才建立並設為 0700；既有的資料夾與上層資料夾都不動。"""
        directory = self.path.parent
        if directory.is_dir():
            return
        directory.mkdir(mode=History.DIRECTORY_MODE, parents=True, exist_ok=True)
        os.chmod(directory, History.DIRECTORY_MODE)

    def points(self, range_s, now=None, max_points=History.MAX_POINTS):
        now = self.clock() if now is None else now
        return chart_points(self.samples, range_s, now, max_points, self.imported.samples)

    def last_unplugged(self, boot_time, now=None):
        now = self.clock() if now is None else now
        return last_unplugged(self.samples, now, boot_time)
