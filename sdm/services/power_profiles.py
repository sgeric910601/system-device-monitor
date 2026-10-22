"""電源模式（power-profiles-daemon）：讀取目前與可用的模式、監看變更，並以非同步 D-Bus 切換。

- 先找 org.freedesktop.UPower.PowerProfiles，沒有時改用舊名稱 net.hadess.PowerProfiles。
- 讀取用 Properties.GetAll，只取 ActiveProfile 與 Profiles；屬性改變時重新讀取。
- 切換用 Properties.Set（ActiveProfile），逾時 1.8 秒；失敗只記錄錯誤名稱並回報失敗，
  呼叫端（頂端列選單、電源頁）把選項恢復成原本的模式。不要求互動式授權：
  作用中的本機使用者本來就不需要密碼。
- 所有呼叫都是非同步的，不會卡住介面；匯流排由外部提供，測試以假的匯流排代替。
"""
from dataclasses import dataclass
import logging
from typing import Optional

from gi.repository import Gio, GLib

from sdm.constants import Battery, Collect
from sdm.core.fmt import PLACEHOLDER
from sdm.data.sources import clean_text
from sdm.data.upower import PROFILES, PROPERTIES_INTERFACE

LOG = logging.getLogger(__name__)
ORDER = ("power-saver", "balanced", "performance")
TITLES = {"power-saver": "省電", "balanced": "平衡", "performance": "效能"}
ACTIVE = "ActiveProfile"


@dataclass(frozen=True)
class ProfileState:
    available: tuple = ()           # 服務回報的模式（依省電、平衡、效能排序）
    active: Optional[str] = None


def profile_title(name):
    if name is None:
        return PLACEHOLDER
    return TITLES.get(name) or clean_text(str(name), Battery.PROFILE_MAX_CHARS) or PLACEHOLDER


def parse_properties(values):
    profiles = values.get("Profiles")
    names = set()
    if isinstance(profiles, (list, tuple)):
        names = {item.get("Profile") for item in profiles if isinstance(item, dict)}
    active = values.get(ACTIVE)
    return ProfileState(tuple(name for name in ORDER if name in names),
                        active if isinstance(active, str) and active in ORDER else None)


class PowerProfiles:
    def __init__(self, bus, on_change, timeout_ms=Collect.DBUS_TIMEOUT_MS):
        self.bus, self.on_change, self.timeout_ms = bus, on_change, timeout_ms
        self.state, self.service, self.subscription = ProfileState(), None, None
        self.stopped = False

    def start(self):
        if self.bus is not None:
            self.read(0)

    def read(self, index):
        destination, path, interface = PROFILES[index]
        self.bus.call(destination, path, PROPERTIES_INTERFACE, "GetAll",
                      GLib.Variant("(s)", (interface,)), GLib.VariantType("(a{sv})"),
                      Gio.DBusCallFlags.NO_AUTO_START, self.timeout_ms, None, self.on_read,
                      index)

    def on_read(self, bus, result, index):
        if self.stopped:
            return
        try:
            values = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            if self.service is None and index + 1 < len(PROFILES):
                self.read(index + 1)
            elif self.service is None:
                self.update(ProfileState())
            return
        if self.service is None:
            self.service = PROFILES[index]
            self.subscribe()
        self.update(parse_properties(values))

    def subscribe(self):
        destination, path, interface = self.service
        self.subscription = self.bus.signal_subscribe(
            destination, PROPERTIES_INTERFACE, "PropertiesChanged", path, interface,
            Gio.DBusSignalFlags.NONE, self.on_signal)

    def on_signal(self, _bus, _sender, _path, _interface, _signal, _parameters):
        if not self.stopped and self.service is not None:
            self.read(PROFILES.index(self.service))

    def update(self, state):
        self.state = state
        self.on_change(state)

    def set(self, profile, done):
        """切換到 profile；done(成功與否)。服務不存在或模式不可用時直接回報失敗。"""
        if self.stopped or self.service is None or profile not in self.state.available:
            done(False)
            return
        destination, path, interface = self.service
        self.bus.call(destination, path, PROPERTIES_INTERFACE, "Set",
                      GLib.Variant("(ssv)", (interface, ACTIVE, GLib.Variant("s", profile))),
                      None, Gio.DBusCallFlags.NONE, self.timeout_ms, None, self.on_set,
                      (profile, done))

    def on_set(self, bus, result, data):
        profile, done = data
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            LOG.warning("Cannot switch the power mode: %s",
                        Gio.DBusError.get_remote_error(error) or "error")
            done(False)
            return
        if not self.stopped:
            self.update(ProfileState(self.state.available, profile))
        done(True)

    def stop(self):
        self.stopped = True
        if self.subscription is not None:
            self.bus.signal_unsubscribe(self.subscription)
            self.subscription = None


class DemoProfiles:
    """示範模式：只在記憶體中切換，不連系統匯流排。active 為 None 代表沒有電源模式服務。"""

    def __init__(self, active, on_change):
        self.on_change = on_change
        self.state = ProfileState(ORDER, active) if active else ProfileState()

    def start(self):
        self.on_change(self.state)

    def set(self, profile, done):
        if profile not in self.state.available:
            done(False)
            return
        self.state = ProfileState(self.state.available, profile)
        self.on_change(self.state)
        done(True)

    def stop(self):
        return None
