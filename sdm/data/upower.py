"""UPower 與電源模式服務（power-profiles-daemon）的唯讀讀取。

- UPower 的 DisplayDevice 是所有電池合併後的預估，當作「系統預估」使用；其他數值以 sysfs 為準。
- 只逐一讀取列出的屬性（不用 GetAll），絕不讀序號、型號或裝置路徑。
- 呼叫時不自動啟動沒有在執行的服務；任何錯誤都視為讀不到。
- 任何一個呼叫逾時，這次取樣其餘的呼叫都略過，30 秒內不再呼叫（UpowerSource）。
讀取函式接受 get（服務、路徑、介面、屬性），測試以假的屬性表代替系統匯流排。
"""
from dataclasses import dataclass
import logging
import math
import time
from typing import Optional

from gi.repository import Gio, GLib

from sdm.constants import Battery, Collect
from sdm.core.battery import round_half_up
from sdm.core.estimates import SystemTimes
from sdm.core.fmt import clean_text

DESTINATION = "org.freedesktop.UPower"
MANAGER_PATH = "/org/freedesktop/UPower"
MANAGER_INTERFACE = "org.freedesktop.UPower"
DISPLAY_PATH = "/org/freedesktop/UPower/devices/DisplayDevice"
DEVICE_INTERFACE = "org.freedesktop.UPower.Device"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
PROFILES = (
    ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
     "org.freedesktop.UPower.PowerProfiles"),
    ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
)
LOG = logging.getLogger(__name__)
TIMEOUT_ERRORS = ("org.freedesktop.DBus.Error.NoReply", "org.freedesktop.DBus.Error.Timeout",
                  "org.freedesktop.DBus.Error.TimedOut")
TYPE_BATTERY = 2              # UPower 裝置類型：電池（3 是不斷電系統）
STATE_MAX = 6
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class UpowerReading:
    on_battery: Optional[bool] = None
    is_battery: bool = False              # DisplayDevice 是筆電電池（不是不斷電系統）
    state: Optional[int] = None
    percentage: Optional[float] = None
    time_to_empty_s: Optional[int] = None  # 0（還在估算）或無效時為 None
    time_to_full_s: Optional[int] = None
    energy_rate_w: Optional[float] = None

    def times(self):
        """轉成估算用的系統預估（分鐘）；DisplayDevice 不是電池時沒有系統預估。"""
        if not self.is_battery:
            return SystemTimes()
        return SystemTimes(reports=True, to_empty_min=minutes(self.time_to_empty_s),
                           to_full_min=minutes(self.time_to_full_s))


def minutes(seconds):
    return None if seconds is None else round_half_up(seconds / SECONDS_PER_MINUTE)


def bus_getter(bus):
    """系統匯流排的屬性讀取函式；bus 為 None 時一律讀不到。"""
    def get(destination, path, interface, name):
        if bus is None:
            raise GLib.Error("System bus unavailable")
        reply = bus.call_sync(destination, path, PROPERTIES_INTERFACE, "Get",
                              GLib.Variant("(ss)", (interface, name)), GLib.VariantType("(v)"),
                              Gio.DBusCallFlags.NO_AUTO_START, Collect.DBUS_TIMEOUT_MS, None)
        return reply.unpack()[0]
    return get


def number(value, low, high):
    real = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if real and math.isfinite(value) and low <= value <= high else None


def seconds(value):
    value = number(value, 1, Battery.TIME_MAX_SECONDS)
    return None if value is None else int(value)


def device_fields(get):
    def prop(name):
        return get(DESTINATION, DISPLAY_PATH, DEVICE_INTERFACE, name)
    state = number(prop("State"), 0, STATE_MAX)
    return dict(state=None if state is None else int(state),
                percentage=number(prop("Percentage"), 0, Battery.PERCENT),
                time_to_empty_s=seconds(prop("TimeToEmpty")),
                time_to_full_s=seconds(prop("TimeToFull")),
                energy_rate_w=number(prop("EnergyRate"), 0, Battery.MAX_W))


def read_upower(get):
    """UPower 沒有執行時回傳 None；DisplayDevice 不是電池時只回傳是否用電池。"""
    try:
        on_battery = get(DESTINATION, MANAGER_PATH, MANAGER_INTERFACE, "OnBattery")
        kind = get(DESTINATION, DISPLAY_PATH, DEVICE_INTERFACE, "Type")
        present = get(DESTINATION, DISPLAY_PATH, DEVICE_INTERFACE, "IsPresent")
    except GLib.Error:
        return None
    on_battery = on_battery if isinstance(on_battery, bool) else None
    if kind != TYPE_BATTERY or present is not True:
        return UpowerReading(on_battery=on_battery)
    try:
        fields = device_fields(get)
    except GLib.Error:
        return UpowerReading(on_battery=on_battery)
    return UpowerReading(on_battery=on_battery, is_battery=True, **fields)


def read_power_profile(get):
    """目前的電源模式（power-saver／balanced／performance）；服務不存在時回傳 None。"""
    for destination, path, interface in PROFILES:
        try:
            value = get(destination, path, interface, "ActiveProfile")
        except GLib.Error:
            continue
        if not isinstance(value, str):
            return None
        return clean_text(value, Battery.PROFILE_MAX_CHARS) or None
    return None


def is_timeout(error):
    if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
        return True
    return Gio.DBusError.get_remote_error(error) in TIMEOUT_ERRORS


class UpowerSource:
    """UPower 與電源模式的讀取，加上逾時保護：一個呼叫逾時（約 1.8 秒）就略過其餘呼叫，
    並在 30 秒內直接回報讀不到，收集執行緒不會每次取樣都被卡住好幾秒。"""

    def __init__(self, get, clock=time.monotonic):
        self.get, self.clock = get, clock
        self.retry_at = None

    def backing_off(self):
        return self.retry_at is not None and self.clock() < self.retry_at

    def guarded(self, destination, path, interface, name):
        if self.backing_off():
            raise GLib.Error.new_literal(Gio.io_error_quark(), "skipped after a timeout",
                                         int(Gio.IOErrorEnum.TIMED_OUT))
        try:
            return self.get(destination, path, interface, name)
        except GLib.Error as error:
            if is_timeout(error):
                self.retry_at = self.clock() + Battery.UPOWER_BACKOFF_SECONDS
                LOG.debug("UPower call timed out; retrying in %d s",
                          Battery.UPOWER_BACKOFF_SECONDS)
            raise

    def upower(self):
        if self.backing_off():
            return None
        reading = read_upower(self.guarded)
        return None if self.backing_off() else reading

    def profile(self):
        if self.backing_off():
            return None
        value = read_power_profile(self.guarded)
        return None if self.backing_off() else value
