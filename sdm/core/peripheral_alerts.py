"""周邊設備低電量通知（純邏輯，不載入 GTK）：每台設備依自己的門檻通知一次（參考專案 PeripheralAlerts）。

- 電量用百分比（含 Solaar 的分段百分比）；電量 ≤ 門檻時通知一次，回到門檻＋2% 以上或門檻設為
  「不通知」（0）才重新啟用。
- 只回報 UPower 電量等級的設備：門檻至少 10% 且等級為低電量或極低電量時算電量低，
  等級回到正常以上才重新啟用。
- 斷線的設備從狀態中移除：重新連上時視為新設備（與參考專案的新設備相同），仍低於門檻就再通知一次。
  設備清單不完整（藍牙或電池資料服務讀取失敗）時不移除任何設備，避免服務短暫中斷就重複通知。
- 門檻存在 preferences.json 的 peripheral_thresholds，鍵為 HMAC-SHA256(salt, 設備識別碼) 的前 16 碼；
  salt 是每次安裝隨機產生的 32 個十六進位字元（peripheral_key_salt），第一次設定門檻時才產生，
  所以檔案裡不出現藍牙位址或裝置路徑，別台電腦也無法用已知位址比對出鍵。
  第一版沒有 salt 的鍵（SHA-256 前 16 碼）在產生 salt 時，對目前看得到的設備換成新鍵，其餘丟掉。
  設為預設的 20% 時移除該項，對照表不會一直變大。
- 記憶體中的通知狀態與頻率限制另用識別碼的 SHA-256（peripheral_key），不寫入任何檔案。
"""
from dataclasses import dataclass
import hashlib
import hmac
import secrets
from typing import Optional

from sdm.constants import Alerts
from sdm.core import alert_messages as messages
from sdm.core.devices import LOW_LEVELS, battery_level, icon_for, reports_charge
from sdm.core.preference_specs import PERIPHERAL_LEVELS
SALT_BYTES = 16

DEFAULT_THRESHOLD = Alerts.PERIPHERAL_DEFAULT
KEY_CHARS = 16
PREFERENCE_KEY = "peripheral_thresholds"
SALT_KEY = "peripheral_key_salt"
OFF = 0
THRESHOLD_CHOICES = tuple((value, "不通知" if value == OFF else f"{value}%")
                          for value in PERIPHERAL_LEVELS)
BELL_CHOICES = tuple((value, "不通知" if value == OFF else f"低於 {value}% 時通知")
                     for value in PERIPHERAL_LEVELS)
BELL_ON_ICON = "preferences-system-notifications-symbolic"
BELL_OFF_ICON = "notifications-disabled-symbolic"


def peripheral_key(device_id):
    """記憶體中的設備鍵（通知狀態、頻率限制），也是第一版沒有 salt 時存進設定檔的鍵。"""
    return hashlib.sha256(str(device_id).encode("utf-8")).hexdigest()[:KEY_CHARS]


def new_salt():
    return secrets.token_hex(SALT_BYTES)


def storage_key(salt, device_id):
    """設定檔的鍵：有 salt 時為 HMAC-SHA256(salt, 識別碼) 前 16 碼，還沒有 salt 時為第一版的鍵。"""
    if not salt:
        return peripheral_key(device_id)
    digest = hmac.new(bytes.fromhex(salt), str(device_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:KEY_CHARS]


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


def threshold_for(thresholds, salt, device_id):
    return threshold_for_key(thresholds, storage_key(salt, device_id))


def threshold_for_key(thresholds, key):
    return thresholds.get(key, DEFAULT_THRESHOLD)


def checked(value):
    if type(value) is not int or value not in PERIPHERAL_LEVELS:
        raise ValueError("Unsupported peripheral threshold")
    return value


def with_all(thresholds, salt, device_ids, value):
    """回傳新的對照表（不修改原本的）；預設值 20% 不另外存。"""
    value = checked(value)
    keys = {storage_key(salt, device_id) for device_id in device_ids}
    updated = {key: level for key, level in thresholds.items() if key not in keys}
    if value != DEFAULT_THRESHOLD:
        updated.update((key, value) for key in sorted(keys))
    return updated


def migrated(thresholds, salt, device_ids):
    """第一版的鍵換成加了 salt 的鍵；只換得了看得到的設備，其餘丟掉。"""
    moved = {}
    for device_id in device_ids:
        old = peripheral_key(device_id)
        if old in thresholds:
            moved[storage_key(salt, device_id)] = thresholds[old]
    return moved


def threshold_changes(preferences, device_ids, value, known_ids=(), make_salt=new_salt):
    """交給 SettingsStore.update() 的變更；還沒有 salt 時產生一個，並轉換看得到的設備的舊鍵。"""
    salt, thresholds, changes = preferences[SALT_KEY], preferences[PREFERENCE_KEY], {}
    if not salt:
        salt = make_salt()
        thresholds = migrated(thresholds, salt, set(known_ids) | set(device_ids))
        changes[SALT_KEY] = salt
    changes[PREFERENCE_KEY] = with_all(thresholds, salt, device_ids, value)
    return changes


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


def evaluate_peripherals(state, readings, thresholds, present, record_only=False, salt=""):
    """present 為連線中所有設備的鍵（None：清單不完整，不移除斷線的設備）；
    thresholds 與 salt 來自設定（peripheral_thresholds、peripheral_key_salt）。"""
    fired = set(state.fired if present is None else state.fired & present)
    alerts = []
    for reading in readings:
        threshold = threshold_for(thresholds, salt, reading.device_id)
        if is_low(reading, threshold):
            if reading.key not in fired and not record_only:
                alerts.append(messages.peripheral_low(reading, threshold))
            fired.add(reading.key)
        elif recovered(reading, threshold):
            fired.discard(reading.key)
    return PeripheralAlertState(frozenset(fired)), tuple(alerts)
