"""純格式化函式：百分比、容量、時間、數字與溫度。"""
import math
import time

PLACEHOLDER = "—"


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
