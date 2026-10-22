"""系統監控頁的數字格式（參考專案 ByteFormat）。

容量與速率用十進位（1 GB＝10⁹ 位元組，與磁碟標示相同），記憶體用二進位但單位仍寫 GB
（與參考專案、系統監視器相同）。總覽頁與頂端列沿用 sdm.core.fmt 的 KiB／MiB 格式不變。
"""
import math

from sdm.core.battery import round_half_up
from sdm.core.fmt import PLACEHOLDER

UNITS = ("B", "KB", "MB", "GB", "TB")
RATE_UNITS = ("B", "KB", "MB", "GB")
COMPACT_UNITS = ("K", "M", "G")
AXIS_UNITS = ("K", "M", "G")
DECIMAL = 1000
BINARY = 1024
ONE_DECIMAL_BELOW = 10          # 小於 10 時保留一位小數（"5.5 GB"），否則取整數（"494 GB"）
DISPLAY_LIMIT = 1000            # 顯示的數字達到 1000 就換下一個單位（1024 進位的 1000～1023 也一樣）
COMPACT_ROUND_UP = 999.5        # 取整後會變成 1000 時就換下一個單位
COMPACT_DECIMAL_BELOW = 9.95    # 精簡速率小於此值時保留一位小數（"1.2M"）
PERCENT = 100


def valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def unit_text(amount, index):
    """位元組取整數；其他單位小於 10 時留一位小數，但進位成 10 時就不留（"10 KB"）。"""
    if index == 0:
        return f"{amount:.0f}"
    text = f"{amount:.1f}"
    return text if float(text) < ONE_DECIMAL_BELOW else f"{amount:.0f}"


def scaled(value, base, units):
    """四捨五入後達到 1000 時改用下一個單位，不會出現「1000 KB」（最大單位除外）。"""
    if not valid(value):
        return PLACEHOLDER
    amount, index = max(0.0, float(value)), 0
    while amount >= base and index < len(units) - 1:
        amount /= base
        index += 1
    text = unit_text(amount, index)
    while float(text) >= DISPLAY_LIMIT and index < len(units) - 1:
        amount /= base
        index += 1
        text = unit_text(amount, index)
    return f"{text} {units[index]}"


def size(value):
    """容量："494 GB"。"""
    return scaled(value, DECIMAL, UNITS)


def memory(value):
    """記憶體："5.5 GB"（以 1024 為進位）。"""
    return scaled(value, BINARY, UNITS)


def rate(value):
    """傳輸速率："1.2 MB/s"。"""
    text = scaled(value, DECIMAL, RATE_UNITS)
    return text if text == PLACEHOLDER else text + "/s"


def compact_rate(value):
    """精簡速率："0K"、"340K"、"1.2M"、"25M"。"""
    if not valid(value):
        return PLACEHOLDER
    amount, index = max(0.0, float(value)) / DECIMAL, 0
    while amount >= COMPACT_ROUND_UP and index < len(COMPACT_UNITS) - 1:
        amount /= DECIMAL
        index += 1
    if 0 < amount < COMPACT_DECIMAL_BELOW and index > 0:
        return f"{amount:.1f}{COMPACT_UNITS[index]}"
    return f"{amount:.0f}{COMPACT_UNITS[index]}"


def axis_rate(value):
    """曲線刻度："0"、"500B"、"2.5M"；低於 1 KB 直接寫位元組，不會被捨入成 "0K"。"""
    amount = max(0.0, float(value)) if valid(value) else 0.0
    if amount == 0:
        return "0"
    if amount < DECIMAL:
        return f"{amount:.0f}B"
    amount, index = amount / DECIMAL, 0
    while amount >= COMPACT_ROUND_UP and index < len(AXIS_UNITS) - 1:
        amount /= DECIMAL
        index += 1
    if amount < COMPACT_DECIMAL_BELOW and round(amount) != amount:
        return f"{amount:.1f}{AXIS_UNITS[index]}"
    return f"{amount:.0f}{AXIS_UNITS[index]}"


def percent(fraction):
    """0～1 的比例 →「20%」（四捨五入）。"""
    if not valid(fraction):
        return PLACEHOLDER
    return f"{round_half_up(fraction * PERCENT)}%"
