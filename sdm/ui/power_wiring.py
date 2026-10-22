"""看板的耗電部分（Monitor 的混入類別）：選出最新的耗電快照、插拔後重新讀取、
記錄歷史、今日回顧與拔電時間，以及頂端列「耗電」指示器。

拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import gi
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, GLib

from sdm.core.recap import DailyRecap, make_recap
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import history_sample, newest
from sdm.ui.power_tray import DESKTOP_ICON, tray_power

POWER_REFRESH = "power_refresh"     # 收集器的方法名稱：插拔後只重新讀取耗電快照


class PowerMixin:
    refresh_pending = False     # 重新讀取進行中又收到新的要求，完成後要再讀一次

    def request_power_refresh(self):
        """插拔後的重新讀取；上一次還在進行時不丟掉，記下來等它完成再補送一次。"""
        if POWER_REFRESH in self.busy:
            self.refresh_pending = True
            return
        self.submit(POWER_REFRESH)

    def power_received(self, kind):
        """收到耗電快照（慢速收集或重新讀取）之後：補送被擋下的重新讀取，並觀察插拔。"""
        if kind == POWER_REFRESH and self.refresh_pending:
            self.refresh_pending = False
            self.submit(POWER_REFRESH)
        self.observe_power()
    def on_uevent(self, _client, _action, device):
        """裝置事件一律更新設備頁；筆電模式下電源的插拔另外立即重新讀取耗電。"""
        self.device_event()
        if isinstance(self.current_power(), LaptopPower) and \
                device.get_subsystem() == "power_supply":
            self.power_refresh.on_event(device.get_property("POWER_SUPPLY_TYPE"),
                                        device.get_property("POWER_SUPPLY_SCOPE"))

    def schedule(self, seconds, callback):
        return GLib.timeout_add(int(seconds * 1000), callback)

    @staticmethod
    def remove_source(source):
        if GLib.MainContext.default().find_source_by_id(source):
            GLib.source_remove(source)

    def observe_power(self):
        """筆電模式：兩次取樣之間接電狀態改變（沒有收到變壓器事件也算）時補排重新讀取。"""
        power = self.current_power()
        if isinstance(power, LaptopPower):
            self.power_refresh.observe(power.battery.ac_online)

    def current_power(self):
        """慢速收集與插拔後重新讀取兩者中較新的耗電快照。"""
        refreshed = self.data.get(POWER_REFRESH, {}).get("power")
        return newest(self.data["slow"].get("power"), refreshed)

    def record_history(self):
        """每分鐘把最新的耗電快照記入歷史；第一次收到快照時也記一筆，同一快照不重複記。"""
        if self.closing:
            return False
        sample = history_sample(self.current_power())
        if sample is not None and sample.t != self.last_recorded:
            self.last_recorded = sample.t
            self.history.add(sample)
            if self.power_page is not None:
                self.power_page.history_changed()
        return True

    def history_points(self, range_s, now):
        """電源頁歷史圖的資料來源。"""
        return self.history.points(range_s, now) if self.history is not None else ()

    def today_recap(self, now):
        return make_recap(self.history.samples, now) if self.history else DailyRecap()

    def history_unplugged(self, now):
        """筆電這次啟動前就拔電時，由歷史紀錄推算拔電時間（關機過則從開機算起）。"""
        return self.history.last_unplugged(self.boot_time, now) if self.history else None

    def render_power_tray(self, power):
        """桌機顯示整機瓦數（選單加上 3 分鐘平均）；筆電顯示電量並換成電池圖示。"""
        state = tray_power(power)
        self.power_indicator.set_label(state.label, "")
        if state.icon != getattr(self, "power_icon", DESKTOP_ICON):
            self.power_icon = state.icon
            self.power_indicator.set_icon_full(state.icon, state.label)
        self.power_indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE
                                        if self.preferences.tray_power
                                        else AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        if self.power_item.get_label() != state.menu:
            self.power_item.set_label(state.menu)
