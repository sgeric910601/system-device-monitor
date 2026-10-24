"""看板的充電上限（Monitor 的混入類別）：電源頁的滑桿、管理員小程式與衝突提醒。

- 開啟程式時只尋找小程式、確認版本與讀取目前的上限（一般權限），絕不要求授權；使用者放開
  滑桿（或停 0.6 秒）或切換「開機與喚醒後保留」時，ChargeControl 才以 pkexec 執行小程式。
- 每次重畫頁面時把最新的耗電快照交給 ChargeControl，使用者沒有在調整時跟著系統的上限。
- 電池可以調整充電上限時，在背景執行緒偵測 TLP、asusd 與 UPower 的充電門檻（開啟程式時，
  以及每次套用成功之後）；只提醒，仍然可以寫入。
- 滑桿出現或消失時重畫詳細資訊（滑桿顯示時不重複列出「充電上限」欄位）；元件狀態改變時
  更新「設定 → 管理員功能」。
- 示範模式改用 DemoChargeControl：不尋找元件、不執行任何程式、不偵測衝突，調整只改記憶體中的值。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging

from gi.repository import GLib

from sdm.constants import ChargeLimit
from sdm.core.charge_limit import normalized
from sdm.data.charge_conflicts import detect_conflicts, upower_thresholds_enabled
from sdm.data.upower import bus_getter
from sdm.services import preview as demo
from sdm.services.charge_control import (APPLIED, ChargeControl, DemoChargeControl,
                                         glib_cancel, glib_schedule)
from sdm.services.privileged import HelperRunner
from sdm.ui import charge_limit_text
from sdm.ui.charge_limit_view import ChargeLimitView

LOG = logging.getLogger(__name__)


def demo_control(scenario, on_change):
    """示範情境的電池門檻（桌機示範沒有電池，滑桿不會出現）。"""
    laptop = demo.LAPTOPS.get(scenario)
    battery = laptop[0] if laptop else None
    limit = normalized(battery.end_threshold) if battery else ChargeLimit.MAX_LIMIT
    start = battery.start_threshold if battery else None
    return DemoChargeControl(on_change, glib_schedule, glib_cancel, limit=limit, start=start)


class ChargeLimitMixin:
    charge_control = None
    charge_view = None
    charge_visible = None           # 上次的滑桿是否顯示
    charge_components = None        # 上次的（安裝方式、通訊協定、電池）
    charge_outcome = ""
    charge_detecting = False

    @staticmethod
    def charge_on_main(function, *args):
        GLib.idle_add(function, *args)

    def build_charge_limit(self):
        """建立滑桿，筆電版面出現時放進電量標題下方；這時還不讀取任何東西。"""
        if self.preview:
            self.charge_control = demo_control(self.preview, self.charge_limit_changed)
        else:
            self.charge_control = ChargeControl(HelperRunner(), self.charge_limit_changed)
        self.charge_view = self.make_charge_view(self.charge_control)
        self.power_page.set_charge_limit(self.charge_view)

    def make_charge_view(self, control):
        return ChargeLimitView(control, self.open_settings)

    def start_charge_limit(self):
        self.charge_control.refresh()
        self.detect_charge_conflicts()

    def charge_limit_changed(self):
        control, view = self.charge_control, self.charge_view
        if control is None:
            return
        state = control.state
        if view is not None:
            view.sync()
        visible = charge_limit_text.visible(state)
        if visible != self.charge_visible:
            self.charge_visible = visible
            if self.power_page is not None:
                self.power_page.settings_changed()
        components = (state.layout, state.protocol, state.supply)
        window = getattr(self, "settings_window", None)
        if components != self.charge_components:
            self.charge_components = components
            if window is not None:
                window.admin_changed()
        if state.outcome == APPLIED and self.charge_outcome != APPLIED:
            self.detect_charge_conflicts()
        self.charge_outcome = state.outcome

    def detect_charge_conflicts(self):
        """背景執行緒偵測會同時設定充電門檻的程式；示範模式與沒有電池時不偵測。"""
        state = self.charge_control.state if self.charge_control else None
        if state is None or state.supply is None or self.preview or self.closing \
                or self.charge_detecting:
            return
        bus = getattr(getattr(self, "collector", None), "bus", None)
        upower = None if bus is None else \
            (lambda supply: upower_thresholds_enabled(bus_getter(bus), supply))
        self.charge_detecting = True
        future = self.pool.submit(detect_conflicts, state.supply, upower=upower)
        future.add_done_callback(
            lambda result: self.charge_on_main(self.charge_conflicts_found, result))

    def charge_conflicts_found(self, future):
        self.charge_detecting = False
        try:
            conflicts = future.result()
        except Exception as error:
            LOG.warning("Cannot check for charge threshold conflicts: %s", type(error).__name__)
            return False
        if not self.closing:
            self.charge_control.set_conflicts(conflicts)
        return False

    def observe_charge_limit(self):
        if self.charge_control is not None:
            self.charge_control.observe(self.current_power())

    def stop_charge_limit(self):
        if self.charge_control is not None:
            self.charge_control.stop()
