"""周邊設備低電量通知（純邏輯，不載入 GTK）：每台設備依自己的門檻通知一次（參考專案 PeripheralAlerts）。

- 電量用百分比（含 Solaar 的分段百分比）；電量 ≤ 門檻時通知一次，回到門檻＋2% 以上或門檻設為
  「不通知」（0）才重新啟用。
- 只回報 UPower 電量等級的設備：門檻至少 10% 且等級為低電量或極低電量時算電量低，
  等級回到正常以上才重新啟用。
- 斷線的設備從狀態中移除：重新連上時視為新設備（與參考專案的新設備相同），仍低於門檻就再通知一次。
  設備清單不完整（藍牙或電池資料服務讀取失敗）時不移除任何設備，避免服務短暫中斷就重複通知。
- 門檻存在 preferences.json 的 peripheral_thresholds，鍵為設備識別碼的 SHA-256 前 16 碼，
  檔案裡不出現藍牙位址或裝置路徑；設為預設的 20% 時移除該項，對照表不會一直變大。
"""
from dataclasses import dataclass
import hashlib
from typing import Optional

from sdm.constants import Alerts
from sdm.core import alert_messages as messages
from sdm.core.devices import LOW_LEVELS, battery_level, icon_for, reports_charge
from sdm.core.preference_specs import PERIPHERAL_LEVELS

DEFAULT_THRESHOLD = Alerts.PERIPHERAL_DEFAULT
KEY_CHARS = 16
PREFERENCE_KEY = "peripheral_thresholds"
OFF = 0
THRESHOLD_CHOICES = tuple((value, "不通知" if value == OFF else f"{value}%")
                          for value in PERIPHERAL_LEVELS)
BELL_CHOICES = tuple((value, "不通知" if value == OFF else f"低於 {value}% 時通知")
                     for value in PERIPHERAL_LEVELS)
BELL_ON_ICON = "preferences-system-notifications-symbolic"
BELL_OFF_ICON = "notifications-disabled-symbolic"


def peripheral_key(device_id):
    """設定檔與頻率限制使用的鍵：識別碼的 SHA-256 前 16 碼（識別碼可能含藍牙位址）。"""
    return hashlib.sha256(str(device_id).encode("utf-8")).hexdigest()[:KEY_CHARS]


@dataclass(frozen=True)
class PeripheralReading:
    device_id: str                  # 只在記憶體中使用，不寫入設定、日誌或通知
    key: str
    name: str                       # 已清理、遮蔽位址的名稱
    icon: str
    percent: Optional[int] = None
    level_low: Optional[bool] = None    # 只有電量等級時：是否為低電量／極低電量


def peripheral_readings(payload):
    """設備清單中回報電量的周邊（不含不斷電系統），依清單順序。"""
    readings = []
    for device in payload.get("devices", []):
        if device.get("kind") == "UPS" or not reports_charge(device):
            continue
        percent = device.get("percent")
        level = battery_level(device)
        readings.append(PeripheralReading(
            device_id=str(device["id"]), key=peripheral_key(device["id"]),
            name=messages.safe_name(device.get("name", "")), icon=icon_for(device),
            percent=None if percent is None else messages.whole(percent),
            level_low=None if percent is not None else level in LOW_LEVELS))
    return tuple(readings)


def present_keys(payload):
    """目前連線中所有設備的鍵；清單不完整（有錯誤訊息）時回傳 None。"""
    if payload.get("errors"):
        return None
    return frozenset(peripheral_key(device["id"]) for device in payload.get("devices", []))


def threshold_for(thresholds, device_id):
    return threshold_for_key(thresholds, peripheral_key(device_id))


def threshold_for_key(thresholds, key):
    return thresholds.get(key, DEFAULT_THRESHOLD)


def checked(value):
    if type(value) is not int or value not in PERIPHERAL_LEVELS:
        raise ValueError("Unsupported peripheral threshold")
    return value


def with_threshold(thresholds, device_id, value):
    """回傳新的對照表（不修改原本的）；預設值 20% 不另外存。"""
    return with_all(thresholds, (device_id,), value)


def with_all(thresholds, device_ids, value):
    value = checked(value)
    keys = {peripheral_key(device_id) for device_id in device_ids}
    updated = {key: level for key, level in thresholds.items() if key not in keys}
    if value != DEFAULT_THRESHOLD:
        updated.update((key, value) for key in sorted(keys))
    return updated


def threshold_changes(thresholds, device_id, value):
    """交給 SettingsStore.update() 的變更（設定視窗的下拉選單與鈴鐺按鈕共用）。"""
    return {PREFERENCE_KEY: with_threshold(thresholds, device_id, value)}


def bell_icon(threshold):
    return BELL_OFF_ICON if threshold == OFF else BELL_ON_ICON


def bell_tooltip(threshold):
    return "低電量通知：關閉" if threshold == OFF else f"低電量通知：低於 {threshold}% 時通知"


@dataclass(frozen=True)
class PeripheralAlertState:
    fired: frozenset = frozenset()      # 已通知、還沒重新啟用的設備鍵


def is_low(reading, threshold):
    if reading.percent is not None:
        return threshold > OFF and reading.percent <= threshold
    return threshold >= Alerts.LEVEL_ONLY_MIN_THRESHOLD and bool(reading.level_low)


def recovered(reading, threshold):
    if threshold == OFF:
        return True
    if reading.percent is not None:
        return reading.percent > threshold + Alerts.REARM_MARGIN
    return reading.level_low is False


def evaluate_peripherals(state, readings, thresholds, present, record_only=False):
    """present 為連線中所有設備的鍵（None：清單不完整，不移除斷線的設備）。"""
    fired = set(state.fired if present is None else state.fired & present)
    alerts = []
    for reading in readings:
        threshold = threshold_for_key(thresholds, reading.key)
        if is_low(reading, threshold):
            if reading.key not in fired and not record_only:
                alerts.append(messages.peripheral_low(reading, threshold))
            fired.add(reading.key)
        elif recovered(reading, threshold):
            fired.discard(reading.key)
    return PeripheralAlertState(frozenset(fired)), tuple(alerts)
