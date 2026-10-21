"""耗電與電量歷史的檔案服務：載入、每 15 筆存檔、結束時存檔。

保護規則：
- 檔案完全無法解讀時，先把原始位元組複製成新的 history-<epoch>.json.bak
  （同名已存在就加 -1、-2…，絕不覆寫），原檔留到下次存檔才被取代。
- 無法讀取、無法備份或來自較新版本時，本次執行停用寫入，原檔不動。
- 個別無效的列（非有限值、超出範圍）在載入時捨棄；時間在未來的列保留。
- 時鐘落後（現在早於最新一筆）時不修剪；修剪若會刪到既有最新一筆前 24 小時內的
  資料（時鐘大幅往前跳），本次執行第一次發生時先另存備份再修剪。
"""
import logging
from pathlib import Path
import time

from sdm import paths
from sdm.constants import History
from sdm.core.history import (NewerFormat, Undecodable, chart_points, decode_history,
                              encode_history, last_unplugged, parse_boot_time, trim,
                              valid_sample)
from sdm.core.jsonfile import create_backup, remove_stale_temporaries, write_bytes_atomic
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
                 backup=create_backup):
        self.path = Path(path) if path is not None else default_path()
        self.clock, self.writer, self.backup = clock, writer, backup
        self.samples = ()
        self.unsaved = 0
        self.writable = True
        self.jump_backup_done = False

    def load(self):
        remove_stale_temporaries(self.path)
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            self._disable("History file unreadable (%s)", type(error).__name__)
            return
        now = self.clock()
        try:
            samples, dropped = decode_history(data)
        except NewerFormat:
            self._disable("History file uses a newer format")
            return
        except Undecodable:
            self._recover(data, now)
            return
        if dropped:
            LOG.warning("Dropped %d invalid history rows", dropped)
        newest = samples[-1].t if samples else None
        self.samples = self._trim(samples, newest, now, data)

    def _disable(self, message, *args):
        self.writable = False
        LOG.warning(message + "; history saving disabled for this session", *args)

    def _recover(self, data, now):
        try:
            backup = self.backup(self.path, data, int(now))
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
        if self.jump_backup_done:
            return True
        try:
            backup = self.backup(self.path, data, int(now))
        except OSError as error:
            self._disable("Clock jumped forward and history could not be backed up (%s)",
                          type(error).__name__)
            return False
        self.jump_backup_done = True
        LOG.warning("Clock jumped forward; copied history to %s before trimming", backup.name)
        return True

    def save(self):
        """有未存的紀錄且允許寫入時原子寫檔；失敗時保留紀錄，下次再試。"""
        if not self.writable or not self.unsaved:
            return False
        try:
            self.path.parent.mkdir(mode=History.DIRECTORY_MODE, parents=True, exist_ok=True)
            self.writer(self.path, encode_history(self.samples))
        except (OSError, ValueError) as error:
            LOG.warning("Cannot save history: %s", type(error).__name__)
            return False
        self.unsaved = 0
        return True

    def points(self, range_s, now=None, max_points=History.MAX_POINTS):
        now = self.clock() if now is None else now
        return chart_points(self.samples, range_s, now, max_points)

    def last_unplugged(self, boot_time, now=None):
        now = self.clock() if now is None else now
        return last_unplugged(self.samples, now, boot_time)
