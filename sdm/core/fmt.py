"""純格式化函式：百分比、容量、時間、數字、溫度，以及清理與遮蔽外部文字。"""
import math
import re
import time
import unicodedata

PLACEHOLDER = "—"
MAC_ADDRESS = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}(?![0-9a-f])")
MAC_MASK = "xx:xx:xx:xx:xx:xx"
IPV4_ADDRESS = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
IPV4_MASK = "x.x.x.x"
_GROUP = r"[0-9a-f]{1,4}"
_GROUPS = rf"{_GROUP}(?::{_GROUP})*"
# 完整的 8 組，或含「::」（至少一邊有數字）的縮寫；時間（12:30:45）不算
IPV6_ADDRESS = re.compile(rf"(?i)(?<![0-9a-f:])(?:(?:{_GROUP}:){{7}}{_GROUP}|{_GROUPS}::(?:{_GROUPS})?"
                          rf"|::{_GROUPS})(?![0-9a-f:])")
IPV6_MASK = "x::x"
# 8 個以上英數字、同時有字母與數字的詞，像序號；型號也可能被遮住（寧可多遮）
SERIAL_LIKE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*[0-9])"
                         r"[A-Za-z0-9]{8,}(?![A-Za-z0-9])")
SERIAL_MASK = "xxxx"


def percent(value):
    return PLACEHOLDER if value is None else f"{value:.0f}%"


def size(value, rate=False):
    if value is None:
        return PLACEHOLDER
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = max(0.0, float(value))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" + ("/s" if rate else "")
        value /= 1024


def age(stamp):
    if not stamp:
        return "尚無回報時間"
    seconds = max(0, int(time.time() - stamp))
    if seconds < 60:
        return "剛剛回報"
    if seconds < 3600:
        return f"{seconds // 60} 分鐘前回報"
    return f"{seconds // 3600} 小時前回報"


def number(text):
    try:
        value = float(str(text).split()[0])
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, IndexError):
        return None


def temperature(value):
    return PLACEHOLDER if value is None else f"{value:.0f}°C"


def value_text(value, suffix=""):
    return PLACEHOLDER if value is None else f"{value:.1f}{suffix}"


def clean_text(value, limit):
    """把控制字元換成空白、合併連續空白，最多保留 limit 個字元。"""
    printable = "".join(" " if unicodedata.category(char).startswith("C") else char
                        for char in str(value))
    return " ".join(printable.split())[:limit]


def mask_addresses(text):
    """把像 MAC 位址（: - _ 分隔）、IPv4、IPv6 位址與序號的部分換成 x，
    用於通知與日誌等會留下的文字。"""
    for pattern, mask in ((MAC_ADDRESS, MAC_MASK), (IPV4_ADDRESS, IPV4_MASK),
                          (IPV6_ADDRESS, IPV6_MASK), (SERIAL_LIKE, SERIAL_MASK)):
        text = pattern.sub(mask, text)
    return text
