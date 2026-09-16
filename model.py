"""Pure formatting, counter arithmetic and device normalization."""
import math
import time

LEVELS = {3: "低電量", 4: "極低電量", 6: "正常", 7: "充足", 8: "已滿"}
STATES = {0: "狀態未知", 1: "充電中", 2: "電池供電", 3: "電池已空",
          4: "已充滿", 5: "等待充電", 6: "等待放電"}

def percent(value):
    return "—" if value is None else f"{value:.0f}%"

def size(value, rate=False):
    if value is None:
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = max(0.0, float(value))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" + ("/s" if rate else "")
        value /= 1024

def battery(level, value, valid=True):
    if level in LEVELS:
        return "電量等級：" + LEVELS[level], None
    if valid and value is not None and math.isfinite(value) and 0 <= value <= 100:
        return percent(value), value
    return "電量未回報", None

def age(stamp):
    if not stamp:
        return "尚無回報時間"
    seconds = max(0, int(time.time() - stamp))
    if seconds < 60:
        return "剛剛回報"
    if seconds < 3600:
        return f"{seconds // 60} 分鐘前回報"
    return f"{seconds // 3600} 小時前回報"

class Counters:
    def __init__(self):
        self.previous = {}

    def rate(self, key, value, now):
        previous = self.previous.get(key)
        self.previous[key] = (value, now)
        if previous is None:
            return None
        old, stamp = previous
        elapsed = now - stamp
        # Suspend/resume and interface/counter replacement require a new baseline.
        if elapsed <= 0 or elapsed > 15 or value < old:
            return None
        return (value - old) / elapsed

class CpuCounter:
    def __init__(self):
        self.previous = None

    def sample(self, values, now):
        total = sum(values[:8])  # guest time is already included in user/nice
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        old = self.previous
        self.previous = (total, idle, now)
        if old is None or now - old[2] > 15:
            return None
        delta, idle_delta = total - old[0], idle - old[1]
        if delta <= 0 or idle_delta < 0:
            return None
        return max(0, min(100, (delta - idle_delta) / delta * 100))

def parse_apc(text):
    data = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data

def number(text):
    try:
        value = float(str(text).split()[0])
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, IndexError):
        return None

def normalize_usb(vendor, product, manufacturer, name, aliases=None):
    key = f"{vendor}:{product}|{manufacturer}|{name}"
    custom = (aliases or {}).get(key)
    if isinstance(custom, dict) and all(isinstance(custom.get(k), str)
                                         for k in ("name", "status", "battery")):
        return custom["name"], custom["status"], custom["battery"]
    if (vendor, product) == ("1532", "054e"):
        return "Razer Barracuda X 接收器", "接收器已連接 · 耳機無線狀態未知", "有電池 · 電量未回報"
    return name or f"USB {vendor}:{product}", "USB 已連接", "電量不適用／未回報"


def disk_is_physical(name):
    return not name.startswith(("loop", "ram", "zram", "dm-", "md", "sr"))
