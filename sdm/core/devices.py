"""裝置正規化：電量等級、選單列、USB 名稱、UPS 與磁碟判斷。"""
import math

from sdm.core.fmt import percent

LEVELS = {3: "低電量", 4: "極低電量", 6: "正常", 7: "充足", 8: "已滿"}
LEVEL_PREFIX = "電量等級："
BATTERY_LOADING = "讀取中…"
NO_BATTERY = "目前沒有設備回報電量"
STATES = {0: "狀態未知", 1: "充電中", 2: "電池供電", 3: "電池已空",
          4: "已充滿", 5: "等待充電", 6: "等待放電"}


def battery(level, value, valid=True):
    if level in LEVELS:
        return LEVEL_PREFIX + LEVELS[level], None
    if valid and value is not None and math.isfinite(value) and 0 <= value <= 100:
        return percent(value), value
    return "電量未回報", None


def reports_charge(device):
    """有百分比或 battery() 的電量等級才算回報；「未回報」標籤兩者皆無。"""
    return device["percent"] is not None or device["battery"].startswith(LEVEL_PREFIX)


def battery_menu_rows(payload):
    """頂端列選單中回報電量的周邊，每台一行；UPS 另有專屬一行。"""
    rows = [(d["id"], f"{d['name']} · {d['battery']}") for d in payload.get("devices", [])
            if d["kind"] != "UPS" and reports_charge(d)]
    if rows:
        return rows
    if payload.get("errors"):
        return [(None, " · ".join(payload["errors"]))]
    return [(None, NO_BATTERY if "stamp" in payload else BATTERY_LOADING)]


def parse_apc(text):
    data = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data


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
