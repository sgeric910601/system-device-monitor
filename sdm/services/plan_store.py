"""智慧充電的排程檔與紀錄檔（只存在這台電腦，不載入 GTK）。

排程檔（XDG 資料目錄的 charging-plan.json）：
- 原子寫入（暫存檔＋fsync＋取代），檔案 0600；資料夾不存在時才建立並設為 0700。
- 無法解讀時，先把原始位元組存成新的 charging-plan-<epoch>.json.bak（同名已存在就加 -1、-2…，
  絕不覆寫），原檔留到下次存檔才被取代；備份失敗、讀不到或來自較新版本時，這次執行不寫入。
- 超過 64 KB 時不讀取，分段複製成備份後視為沒有排程。
紀錄檔（XDG 狀態目錄的 charging-plan.log）：每次判斷的狀態變化、寫入的上限與讀回結果、
喚醒排程，每行一筆、開頭是本機時間；以 O_APPEND｜O_NOFOLLOW 附加，超過 256 KB 時只留後半
（從完整的一行開始）。只寫百分比、時間與狀態，不寫電池名稱、序號或帳號。
示範模式與模擬測試改用 in_memory／MemoryLog，不讀寫任何檔案。
"""
from datetime import datetime
import logging
import os
from pathlib import Path
import time

from sdm import paths
from sdm.constants import SmartCharging as Limits
from sdm.core.jsonfile import (create_backup, create_backup_copy, remove_stale_temporaries,
                               write_bytes_atomic)
from sdm.core.plan_format import NewerFormat, Undecodable, decode_plan, encode_plan

LOG = logging.getLogger(__name__)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def default_plan_path():
    return paths.DATA/Limits.PLAN_FILE


def default_log_path():
    return paths.STATE/Limits.LOG_FILE


def ensure_directory(directory):
    """資料夾不存在時才建立並設為 0700；既有的資料夾不動。"""
    if directory.is_dir():
        return
    directory.mkdir(mode=Limits.DIRECTORY_MODE, parents=True, exist_ok=True)
    os.chmod(directory, Limits.DIRECTORY_MODE)


class PlanStore:
    """只在主執行緒使用。"""

    def __init__(self, path=None, clock=time.time, writer=write_bytes_atomic,
                 backup=create_backup, copy_backup=create_backup_copy, memory=False):
        self.path = None if memory else Path(path) if path is not None else default_plan_path()
        self.clock, self.writer = clock, writer
        self.backup, self.copy_backup = backup, copy_backup
        self.memory, self.writable, self.warned = memory, True, False
        self.plan = None

    @classmethod
    def in_memory(cls, plan=None):
        store = cls(memory=True)
        store.plan = plan
        return store

    def load(self):
        if self.memory:
            return self.plan
        remove_stale_temporaries(self.path)
        try:
            if self.path.stat().st_size > Limits.PLAN_MAX_BYTES:
                self.recover(None)
                return None
            data = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            self.disable("Charging plan unreadable (%s)", type(error).__name__)
            return None
        return self.decode(data)

    def decode(self, data):
        try:
            return decode_plan(data)
        except NewerFormat:
            self.disable("Charging plan uses a newer format")
        except Undecodable:
            self.recover(data)
        return None

    def recover(self, data):
        """無法解讀（data）或太大（None，分段複製）：另存新的備份；失敗就停用寫入。"""
        epoch = int(self.clock())
        try:
            if data is None:
                backup = self.copy_backup(self.path, epoch, mode=Limits.FILE_MODE)
            else:
                backup = self.backup(self.path, data, epoch, mode=Limits.FILE_MODE)
        except OSError as error:
            self.disable("Charging plan is invalid and could not be backed up (%s)",
                         type(error).__name__)
            return
        LOG.warning("Charging plan is invalid; copied to %s and starting without a plan",
                    backup.name)

    def disable(self, message, *args):
        self.writable = False
        LOG.warning(message + "; not saving the charging plan in this session", *args)

    def save(self, plan):
        if self.memory:
            self.plan = plan
            return True
        if not self.writable:
            return False
        try:
            ensure_directory(self.path.parent)
            self.writer(self.path, encode_plan(plan), mode=Limits.FILE_MODE)
        except (OSError, ValueError) as error:
            level = logging.DEBUG if self.warned else logging.WARNING
            LOG.log(level, "Cannot save the charging plan (%s)", type(error).__name__)
            self.warned = True
            return False
        return True


def stamp_text(now):
    return datetime.fromtimestamp(now).astimezone().strftime(TIME_FORMAT)


def line_text(now, message):
    return stamp_text(now) + " " + " ".join(str(message).split()) + "\n"


def second_half(data):
    """只留後半，從完整的一行開始。"""
    rest = data[-(Limits.LOG_MAX_BYTES // 2):]
    newline = rest.find(b"\n")
    return rest[newline + 1:] if 0 <= newline < len(rest) - 1 else rest


class PlanLog:
    """紀錄檔；寫入失敗只記錄一次警告，不影響排程。"""

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path is not None else default_log_path()
        self.clock, self.warned = clock, False

    def write(self, message):
        try:
            ensure_directory(self.path.parent)
            self.trim()
            fd = os.open(self.path, APPEND_FLAGS, Limits.FILE_MODE)
            try:
                os.write(fd, line_text(self.clock(), message).encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as error:
            level = logging.DEBUG if self.warned else logging.WARNING
            LOG.log(level, "Cannot write the charging plan log (%s)", type(error).__name__)
            self.warned = True

    def read(self):
        """紀錄檔的內容（不跟隨符號連結）；不存在或讀不到時為 None。"""
        try:
            fd = os.open(self.path, READ_FLAGS)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as handle:
            return handle.read(2 * Limits.LOG_MAX_BYTES)

    def trim(self):
        try:
            if self.path.lstat().st_size <= Limits.LOG_MAX_BYTES:
                return
        except FileNotFoundError:
            return
        data = self.read()
        if data is not None:
            write_bytes_atomic(self.path, second_half(data), mode=Limits.FILE_MODE)

    def tail(self, count=Limits.LOG_TAIL_LINES):
        data = self.read()
        if not data:
            return []
        return data.decode("utf-8", "replace").splitlines()[-count:]


class MemoryLog:
    """示範模式與模擬測試：紀錄只放在記憶體。"""

    def __init__(self, clock=time.time):
        self.clock, self.lines = clock, []

    def write(self, message):
        self.lines.append(line_text(self.clock(), message).rstrip("\n"))
        del self.lines[:-Limits.LOG_TAIL_LINES]

    def tail(self, count=Limits.LOG_TAIL_LINES):
        return self.lines[-count:]
