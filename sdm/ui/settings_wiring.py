"""看板的設定部分（Monitor 的混入類別）：設定改變時立即套用，以及開啟設定視窗。

SettingsStore 通知改變的鍵後（不需要重新啟動）：
- 系統監控模組 → 頁面、總覽卡片、取樣與頂端列（schedule_wiring.apply_modules）。
- 更新頻率與省電模式 → 重新排程（schedule_wiring.apply_cadence）。
- 頂端列相關（顯示方式、格式、項目）→ 重新顯示頂端列。
- 隱藏的電力流向細項或詳細欄位 → 電源頁重畫。
- 周邊設備的通知門檻（或設定檔變成唯讀）→ 連線設備頁的鈴鐺更新；寫入被拒絕時鈴鐺的提示
  與設定 → 周邊設備寫出原因。
- 管理員 CPU 功耗取樣 → 以 pkexec 開關服務（cpu_power_wiring）。
- 程式圖示的顏色（以及之後的夥伴與桌面寵物）→ appearance_wiring。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
from sdm import paths
from sdm.core.peripheral_alerts import PREFERENCE_KEY, SALT_KEY, peripheral_readings
from sdm.core.schedule import external_power
from sdm.core.settings import LEGACY_KEYS
from sdm.data.power_supply import read_batteries
from sdm.services.battery_service import LaptopPower
from sdm.services.preview import DESKTOP
from sdm.services.settings_store import STATUS
from sdm.ui.first_run import FirstRunWindow, should_ask
from sdm.ui.schedule_wiring import CADENCE_KEYS, MODULE_KEYS
from sdm.ui.settings_window import SettingsContext, SettingsWindow

TRAY_KEYS = frozenset(LEGACY_KEYS) | {"tray_mode", "tray_display", "tray_power",
                                      "low_threshold"}
HIDDEN_KEYS = frozenset({"hidden_flow_items", "hidden_detail_fields"})
BELL_KEYS = frozenset({PREFERENCE_KEY, SALT_KEY, STATUS})
ADMIN_KEY = "admin_cpu_sampling"


class SettingsMixin:
    settings_window = None
    store = None
    devices_page = None
    peripheral_settings = None      # PeripheralSettings：周邊門檻的唯一寫入點

    def settings_changed(self, changed, store):
        self.settings = dict(store.legacy)
        self.preferences = store.preferences
        if self.scheduler is not None and changed & MODULE_KEYS:
            self.apply_modules()
        elif self.scheduler is not None and changed & CADENCE_KEYS:
            self.apply_cadence()
        if changed & TRAY_KEYS:
            self.render_indicator()
        if changed & HIDDEN_KEYS and self.power_page is not None:
            self.power_page.settings_changed()
        if changed & BELL_KEYS and self.devices_page is not None:
            self.devices_page.thresholds_changed()
        if ADMIN_KEY in changed:
            self.cpu_power_preference_changed()
        self.appearance_changed(changed)

    def set_peripheral_threshold(self, device_id, value):
        """連線設備頁的鈴鐺：寫入門檻；被拒絕時由 peripheral_problem_changed() 恢復鈴鐺並說明原因。"""
        self.peripheral_settings.choose((device_id,), value)

    def peripheral_problem(self):
        problem = self.peripheral_settings.problem if self.peripheral_settings else None
        return problem or ""

    def peripheral_problem_changed(self):
        """周邊門檻寫入被拒絕（或原因清除）：鈴鐺與設定視窗恢復實際的值並顯示原因。"""
        if self.devices_page is not None:
            self.devices_page.thresholds_changed()
        if self.settings_window is not None:
            self.settings_window.peripheral_problem_changed()

    def flush_settings(self):
        if self.store is not None:
            self.store.flush()

    def is_laptop(self):
        """有系統電池：依最新的耗電快照；還沒有快照時直接看 sysfs（示範模式看情境）。"""
        power = self.current_power()
        if power is not None:
            return isinstance(power, LaptopPower)
        if self.preview:
            return self.preview != DESKTOP
        return bool(read_batteries())

    def ask_first_run(self):
        """首次啟動（舊版設定檔原本不存在）時詢問監控範圍；模擬測試與示範模式不問。"""
        if should_ask(self.first_run, self.smoke, self.preview):
            FirstRunWindow(self, self.store, self.is_laptop()).present()
        return False

    def open_settings(self, section=None):
        if self.settings_window is None:
            context = SettingsContext(
                laptop=self.is_laptop(), root=paths.ROOT, version=paths.VERSION,
                external_power=lambda: external_power(self.current_power()),
                ups_present=self.ups_present,
                peripherals=lambda: peripheral_readings(self.data["devices"]),
                peripheral_settings=self.peripheral_settings,
                notice=self.notice_text, send_test=self.send_test_notification,
                probe=self.probe_notifications, notifications_enabled=self.notifier is not None,
                admin=self.admin, companion=self.companion_context(),
                charge_limit=self.charge_control)
            self.settings_window = SettingsWindow(self, self.store, context)
        if self.admin is not None:
            self.admin.update_socket()
        self.settings_window.present(section)
