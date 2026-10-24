"""充電上限的唯讀讀取（一般權限）：可調整的系統電池、目前的門檻，以及保留的狀態檔。

- 電池：依名稱排序的第一顆系統電池（type 為 Battery、scope 不是 Device、present 不是 0），
  而且有 charge_control_end_threshold；名稱必須是管理員小程式接受的格式（英數與底線，1–32 字）。
- 門檻：0–100 的整數；讀不到或不合理時為 None。
- 狀態檔：sdm-charge-limit --persist on 寫入、所有人可讀；以 O_NOFOLLOW｜O_NONBLOCK 開啟、
  4 KB 以內，格式與小程式完全相同才採用，否則視為沒有保留。
所有函式都接受 root，測試可以指向假的 /sys 目錄。
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Optional

from sdm.constants import Battery, ChargeLimit
from sdm.data.power_supply import is_system_battery, supply_directories
from sdm.data.sources import read

END_FILE = "charge_control_end_threshold"
START_FILE = "charge_control_start_threshold"
SUPPLY = re.compile(ChargeLimit.SUPPLY_PATTERN)
STATE_KEYS = frozenset({"format", "supply", "end", "start"})
MAX_START = ChargeLimit.MAX_LIMIT - 1
PERCENT_MAX_DIGITS = 3
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@dataclass(frozen=True)
class Thresholds:
    supply: str
    end: Optional[int]
    start: Optional[int]
    has_start: bool                     # 驅動有起點檔（ThinkPad 等）


@dataclass(frozen=True)
class SavedLimit:
    """「開機與喚醒後保留」的值。"""
    supply: str
    end: int
    start: Optional[int]


def percent(path):
    text = read(path)
    if not text or len(text) > PERCENT_MAX_DIGITS or not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    return value if value <= Battery.PERCENT else None


def adjustable(directory):
    return bool(SUPPLY.fullmatch(directory.name)) and is_system_battery(directory) \
        and (directory/END_FILE).is_file()


def thresholds(directory):
    return Thresholds(directory.name, percent(directory/END_FILE), percent(directory/START_FILE),
                      (directory/START_FILE).is_file())


def find_battery(root=Path("/")):
    """第一顆可以調整充電上限的系統電池；沒有時為 None。"""
    for directory in supply_directories(root):
        if adjustable(directory):
            return thresholds(directory)
    return None


def read_thresholds(root, supply):
    """指定電池目前的門檻；名稱不合格式或不能調整時為 None。"""
    if not SUPPLY.fullmatch(supply):
        return None
    directory = Path(root)/Battery.SUPPLY_DIR/supply
    return thresholds(directory) if adjustable(directory) else None


def read_small(path):
    try:
        fd = os.open(path, READ_FLAGS)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > ChargeLimit.STATE_MAX_BYTES:
            return None
        data = handle.read(ChargeLimit.STATE_MAX_BYTES + 1)
    return data if len(data) <= ChargeLimit.STATE_MAX_BYTES else None


def is_integer(value):
    return type(value) is int


def valid_state(value):
    if not isinstance(value, dict) or set(value) != STATE_KEYS \
            or not is_integer(value["format"]) or value["format"] != ChargeLimit.STATE_FORMAT:
        return False
    supply, end, start = value["supply"], value["end"], value["start"]
    if not isinstance(supply, str) or not SUPPLY.fullmatch(supply) or not is_integer(end) \
            or not ChargeLimit.MIN_LIMIT <= end <= ChargeLimit.MAX_LIMIT:
        return False
    return start is None or (is_integer(start) and 0 <= start <= MAX_START and start < end)


def read_saved(path):
    """保留的充電上限；沒有、讀不到或格式不符時為 None。"""
    data = None if path is None else read_small(path)
    if data is None:
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not valid_state(value):
        return None
    return SavedLimit(value["supply"], value["end"], value["start"])
