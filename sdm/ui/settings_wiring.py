"""看板的設定部分（Monitor 的混入類別）：設定改變時立即套用，以及開啟設定視窗。

SettingsStore 通知改變的鍵後（不需要重新啟動）：
- 系統監控模組 → 頁面、總覽卡片、取樣與頂端列（schedule_wiring.apply_modules）。
- 更新頻率與省電模式 → 重新排程（schedule_wiring.apply_cadence）。
- 頂端列相關（顯示方式、格式、項目）→ 重新顯示頂端列。
- 隱藏的電力流向細項或詳細欄位 → 電源頁重畫。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
from sdm import paths
from sdm.core.schedule import external_power
from sdm.core.settings import LEGACY_KEYS
from sdm.data.power_supply import read_batteries
from sdm.services.battery_service import LaptopPower
from sdm.services.preview import DESKTOP
from sdm.ui.first_run import FirstRunWindow, should_ask
from sdm.ui.schedule_wiring import CADENCE_KEYS, MODULE_KEYS
from sdm.ui.settings_window import SettingsContext, SettingsWindow

TRAY_KEYS = frozenset(LEGACY_KEYS) | {"tray_mode", "tray_display", "tray_power",
                                      "low_threshold"}
HIDDEN_KEYS = frozenset({"hidden_flow_items", "hidden_detail_fields"})


class SettingsMixin:
    settings_window = None
    store = None

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
            context = SettingsContext(laptop=self.is_laptop(), root=paths.ROOT,
                                      version=paths.VERSION,
                                      external_power=lambda: external_power(self.current_power()))
            self.settings_window = SettingsWindow(self, self.store, context)
        self.settings_window.present(section)
