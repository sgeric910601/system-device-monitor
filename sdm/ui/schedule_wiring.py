"""看板的取樣排程（Monitor 的混入類別）：更新頻率、省電模式與系統監控模組的即時套用。

- 各取樣層的間隔由 sdm.core.schedule 算出，TierScheduler 只重新排程有改變的層，
  舊的計時器一定先移除；新開始的層立即取樣一次。
- 系統監控模組全部關閉時一般層（CPU、記憶體、網速、磁碟讀寫）完全不排程。
- nvidia-smi 串流的間隔只在實際值改變時才重新啟動；耗電服務判斷元件讀值過期的間隔跟著慢速層。
- 省電模式「只在使用電池時」：每次收到耗電快照時檢查外部電源，有改變才重新計算。
- 電力流向與充電夥伴的動畫只在省電模式沒有生效、視窗沒有最小化也沒有被完全遮住時播放。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
from gi.repository import GLib

from sdm.constants import Schedule, Solaar
from sdm.core.schedule import cadence, cadence_for, external_power
from sdm.core.tiers import MODULE_PAGES, enabled_modules
from sdm.services.scheduler import TierScheduler

MODULE_KEYS = frozenset({"system_monitors", "monitor_cpu", "monitor_memory", "monitor_disk",
                         "monitor_network"})
CADENCE_KEYS = frozenset({"refresh_interval", "power_saving", "power_saving_on_battery_only"})


class ScheduleMixin:
    scheduler = None
    cadence = cadence(Schedule.DEFAULT_INTERVAL, False)
    external = None             # 上一次計算時是否接著外部電源

    def create_scheduler(self):
        self.scheduler = TierScheduler(
            lambda seconds, kind: GLib.timeout_add_seconds(seconds, self.tick, kind),
            self.remove_source)

    def tier_intervals(self, current):
        return {"system": current.system if self.collector.modules else None,
                "slow": current.slow, "devices": current.devices, "logitech": Solaar.INTERVAL}

    def apply_cadence(self):
        power = self.current_power()
        self.external = external_power(power)
        current = cadence_for(self.preferences, power)
        for kind in self.scheduler.apply(self.tier_intervals(current)):
            self.submit(kind)
        self.collector.nvidia.set_interval_ms(current.nvidia_ms)
        self.collector.power.interval = current.slow
        detail_changed = current.detail != self.cadence.detail
        self.cadence = current
        if detail_changed and self.detail_timer:
            self.restart_detail_timer(self.detail_gate.request)
        self.update_motion()
        self.update_cpu_power()             # CPU 功耗請求跟著更新頻率
        if getattr(self, "settings_window", None) is not None:
            self.settings_window.external_power_changed()

    def apply_modules(self):
        """模組開關：頁面、總覽卡片、收集器與頂端列一起更新。"""
        modules = enabled_modules(self.preferences)
        self.collector.modules = modules
        for module, page in MODULE_PAGES.items():
            self.main.set_page_visible(page, module in modules)
        self.overview.set_modules(modules)
        self.apply_cadence()
        self.render_indicator()

    def power_changed(self):
        """收到新的耗電快照：外部電源有改變時（插拔、不斷電系統改用電池）重新計算。"""
        if self.scheduler is not None and external_power(self.current_power()) != self.external:
            self.apply_cadence()

    def update_motion(self):
        if self.power_page is not None:
            self.power_page.set_motion(not self.window_obscured, self.window_iconified,
                                       self.cadence.saving)
        self.update_companion_motion()

    def stop_scheduler(self):
        if self.scheduler is not None:
            self.scheduler.stop()
