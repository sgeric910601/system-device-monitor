"""新版設定的鍵值規格表：型別、預設值、選項與範圍集中在這裡。

每個規格的 check(raw) 回傳（正規化後的值，是否捨棄了部分無效項目）；
整個值無效時拋出 ValueError。布林值必須是真正的 bool，整數欄位拒絕 bool。
"""
from collections.abc import Mapping
import math
import re
from types import MappingProxyType
from typing import Callable, NamedTuple

SCHEMA_VERSION = 2
MAX_ITEMS = 64                                    # 清單與對照表的項目上限
NAME_PATTERN = re.compile(r"[a-z_]{1,32}")        # 隱藏項目名稱
PERIPHERAL_KEY_PATTERN = re.compile(r"[0-9a-f]{16}")
SALT_PATTERN = re.compile(r"[0-9a-f]{32}")       # 周邊設備鍵的 HMAC 金鑰（每次安裝隨機產生）
PERIPHERAL_LEVELS = (0, 10, 15, 20, 30, 50)       # 周邊低電量鈴鐺門檻；0 代表關閉


class Spec(NamedTuple):
    default: object
    check: Callable[[object], tuple]
    choices: tuple = ()
    low: object = None
    high: object = None
    step: object = None


def boolean(default):
    def check(raw):
        if type(raw) is not bool:
            raise ValueError("expected a boolean")
        return raw, False
    return Spec(default, check)


def choice(default, options):
    def check(raw):
        if type(raw) is not type(default) or raw not in options:
            raise ValueError("unknown option")
        return raw, False
    return Spec(default, check, choices=tuple(options))


def integer(default, low, high, step=1, also=()):
    """low–high 之間、從 low 起每 step 一格的整數；also 為範圍外另外允許的值。"""
    def check(raw):
        if type(raw) is not int:
            raise ValueError("expected an integer")
        if raw in also:
            return raw, False
        if not low <= raw <= high or (raw - low) % step:
            raise ValueError("out of range")
        return raw, False
    return Spec(default, check, choices=tuple(also), low=low, high=high, step=step)


def real(default, low, high):
    def check(raw):
        if type(raw) not in (int, float) or not math.isfinite(raw) or not low <= raw <= high:
            raise ValueError("out of range")
        return float(raw), False
    return Spec(default, check, low=low, high=high)


def names():
    """字串清單：捨棄不合格項目、去除重複並保留順序；超過上限視為整體無效。"""
    def check(raw):
        if not isinstance(raw, (list, tuple)):
            raise ValueError("expected a list")
        valid = [item for item in raw if isinstance(item, str) and NAME_PATTERN.fullmatch(item)]
        kept = tuple(dict.fromkeys(valid))
        if len(kept) > MAX_ITEMS:
            raise ValueError("too many items")
        return kept, len(valid) != len(raw)
    return Spec((), check)


def thresholds():
    """周邊識別碼 → 門檻：捨棄不合格的鍵或值；超過上限時只留前 64 個合格的項目
    （載入時記錄警告，寫入時視為無效，避免一個太大的檔案讓所有門檻回到預設值）。"""
    def check(raw):
        if not isinstance(raw, Mapping):
            raise ValueError("expected an object")
        valid = [(key, value) for key, value in raw.items()
                 if isinstance(key, str) and PERIPHERAL_KEY_PATTERN.fullmatch(key)
                 and type(value) is int and value in PERIPHERAL_LEVELS]
        kept = dict(valid[:MAX_ITEMS])
        return MappingProxyType(kept), len(kept) != len(raw)
    return Spec(MappingProxyType({}), check, choices=PERIPHERAL_LEVELS)


def salt():
    """32 個小寫十六進位字元；空字串代表還沒產生（第一次設定周邊門檻時才產生）。"""
    def check(raw):
        if type(raw) is not str or (raw and not SALT_PATTERN.fullmatch(raw)):
            raise ValueError("invalid salt")
        return raw, False
    return Spec("", check)


PREFERENCE_SPECS = MappingProxyType({
    "tray_mode": choice("split", ("split", "merged")),
    "tray_display": choice("icon_percent", ("icon_percent", "icon_only", "percent_only",
                                            "icon_time", "icon_percent_time")),
    "refresh_interval": choice(2, (1, 2, 5, 10)),
    "tray_power": boolean(True),                 # 頂端列「耗電」指示器（不寫入舊版 settings.json）
    "power_saving": boolean(False),
    "power_saving_on_battery_only": boolean(False),
    "system_monitors": boolean(True),
    "monitor_cpu": boolean(True),
    "monitor_memory": boolean(True),
    "monitor_disk": boolean(True),
    "monitor_network": boolean(True),
    "hidden_flow_items": names(),
    "hidden_detail_fields": names(),
    "low_alert_enabled": boolean(True),
    "low_threshold": integer(20, 5, 50, step=5),
    "full_alert_enabled": boolean(True),
    "full_threshold": integer(100, 80, 100, step=5),
    "ups_alerts_enabled": boolean(True),
    "ups_low_threshold": integer(50, 10, 90),
    "peripheral_thresholds": thresholds(),
    "peripheral_key_salt": salt(),
    "admin_cpu_sampling": boolean(False),
    "app_icon_color": choice("mint", ("mint", "peach", "night", "sky")),
    "auto_check_updates": boolean(True),
    "update_notification": boolean(True),
    "companion_appearance": choice("cat", ("cat", "buddy", "bar", "headshot", "photo")),
    "desktop_pet": boolean(False),
    "desktop_pet_on_wallpaper": boolean(False),
    "desktop_pet_scale": real(1.0, 0.5, 2.0),
    "desktop_pet_live_eyes": boolean(True),
    "desktop_pet_follow_cursor": boolean(True),
    "charging_target_percent": integer(0, 50, 100, also=(0,)),    # 0 代表不設目標
    "charging_hold_percent": integer(80, 50, 100),
    "charging_sleep_strategy": choice("readiness_first", ("readiness_first", "precise_wake")),
    "charging_wake_enabled": boolean(False),
    "charge_limit_persist": boolean(False),
})
