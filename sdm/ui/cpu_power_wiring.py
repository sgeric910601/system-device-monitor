"""看板的管理員 CPU 功耗取樣（Monitor 的混入類別）：元件狀態、開關與 socket 請求。

- 開啟程式時只尋找元件並確認版本（一般權限），絕不要求授權。選項開著、但服務沒有為
  自己開啟時（例如元件被移除），直接關閉選項並說明，不跳出密碼視窗。
- 使用者在設定裡切換選項時才以 pkexec 開關服務；授權取消或失敗就把選項恢復成實際狀態。
- 只有「選項開著、服務為自己開啟、電源頁顯示中」時才連線，每個更新間隔送一次請求
  （收集執行緒中進行，不卡介面）；條件不成立時停止計時器並送最後一次請求關閉連線。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging

from gi.repository import GLib

from sdm import paths
from sdm.core.settings import PreferencesReadOnlyError
from sdm.services.privileged import AdminComponents, HelperRunner, demo_admin
from sdm.ui import admin_text

CPU_POWER = "cpu_power"             # busy 集合中代表 CPU 功耗請求進行中
PREFERENCE = "admin_cpu_sampling"
POWER_PAGE = "power"
LOG = logging.getLogger(__name__)


class CpuPowerMixin:
    admin = None
    admin_checked = False           # 開啟程式後的選項檢查只做一次
    cpu_power_timer = 0
    cpu_power_interval = None

    @staticmethod
    def on_main(function, *args):
        GLib.idle_add(function, *args)

    def start_admin(self):
        if self.preview:
            self.admin = demo_admin(self.preview, self.admin_changed)
            if self.admin.state.enabled:
                self.apply_preference(True)
        else:
            self.admin = AdminComponents(HelperRunner(), self.admin_changed, root=paths.ROOT)
        self.admin.refresh()

    def admin_changed(self):
        self.check_startup_preference()
        self.update_cpu_power()
        window = getattr(self, "settings_window", None)
        if window is not None:
            window.admin_changed()

    def check_startup_preference(self):
        state = self.admin.state
        if self.admin_checked or (state.layout is not None and state.protocol is None):
            return
        self.admin_checked = True
        if self.preferences.admin_cpu_sampling and not (state.ready and state.enabled):
            self.apply_preference(False)
            self.admin.report(admin_text.STARTUP_OFF)

    def apply_preference(self, value):
        try:
            self.store.update(**{PREFERENCE: value})
        except (PreferencesReadOnlyError, KeyError, ValueError) as error:
            LOG.warning("Cannot change the CPU sampling option: %s", type(error).__name__)

    def cpu_power_preference_changed(self):
        """使用者切換選項：與實際狀態不同時才要求授權。"""
        wanted, state = bool(self.preferences.admin_cpu_sampling), self.admin_state()
        if state is not None and state.ready and not state.busy and wanted != state.enabled:
            self.admin.set_enabled(wanted, self.admin_switched)
        self.update_cpu_power()

    def admin_switched(self, ok):
        state = self.admin_state()
        if not ok and state is not None and bool(self.preferences.admin_cpu_sampling) != \
                state.enabled:
            self.apply_preference(state.enabled)
        self.update_cpu_power()

    def admin_state(self):
        return self.admin.state if self.admin is not None else None

    def cpu_power_client(self):
        return getattr(getattr(self, "collector", None), "cpu_power", None)

    def cpu_power_wanted(self):
        state = self.admin_state()
        return bool(state is not None and state.ready and state.enabled and not self.closing
                    and self.preferences.admin_cpu_sampling
                    and POWER_PAGE in self.visible_pages())

    def update_cpu_power(self):
        """依目前的條件啟用或停用客戶端與計時器（設定、可見頁面或更新頻率改變時呼叫）。"""
        client = self.cpu_power_client()
        if client is None or self.admin is None:
            return
        self.admin.update_socket()
        layout = self.admin.state.layout
        client.configure(layout.socket if layout is not None else None)
        wanted = self.cpu_power_wanted()
        client.set_active(wanted)
        interval = self.cadence.system
        if wanted and (not self.cpu_power_timer or interval != self.cpu_power_interval):
            self.stop_cpu_power_timer()
            self.cpu_power_interval = interval
            self.cpu_power_timer = self.schedule(interval, self.cpu_power_tick)
            self.submit_cpu_power()
        elif not wanted and self.cpu_power_timer:
            self.stop_cpu_power_timer()
            self.submit_cpu_power()             # 這次請求看到已停用，負責關閉連線

    def cpu_power_tick(self):
        if self.closing or not self.cpu_power_wanted():
            self.cpu_power_timer = 0
            return False
        self.submit_cpu_power()
        return True

    def submit_cpu_power(self):
        client = self.cpu_power_client()
        if client is None or CPU_POWER in self.busy or self.closing:
            return
        self.busy.add(CPU_POWER)
        future = self.pool.submit(client.request)
        future.add_done_callback(lambda result: self.on_main(self.cpu_power_done, result))

    def cpu_power_done(self, future):
        self.busy.discard(CPU_POWER)
        try:
            future.result()
        except Exception as error:
            LOG.warning("CPU power request failed: %s", type(error).__name__)
        client = self.cpu_power_client()
        if not self.cpu_power_wanted() and getattr(client, "connected", False):
            self.submit_cpu_power()
        return False

    def stop_cpu_power_timer(self):
        if self.cpu_power_timer:
            self.remove_source(self.cpu_power_timer)
            self.cpu_power_timer = 0

    def stop_cpu_power(self):
        self.stop_cpu_power_timer()
        client = self.cpu_power_client()
        if client is not None:
            client.set_active(False)
        if self.admin is not None:
            self.admin.stop()
