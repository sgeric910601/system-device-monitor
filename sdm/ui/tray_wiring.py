"""看板的頂端列與電源模式（Monitor 的混入類別）。

- 建立共用選單與頂端列控制；每次更新資料或頂端列設定改變時重新計算內容（只送出有改變的部分）。
- 電源模式：讀取與切換都經由 power_profiles（非同步 D-Bus）；示範模式只在記憶體中切換。
  切換失敗時把選單與電源頁的選項恢復成實際的模式。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
from sdm import paths
from sdm.core.devices import battery_menu_rows
from sdm.services import preview as demo
from sdm.services.power_profiles import DemoProfiles, PowerProfiles
from sdm.ui.tray import IndicatorFactory, Tray
from sdm.ui.tray_icons import IconWriter
from sdm.ui.tray_menu import TrayMenu
from sdm.ui.tray_text import tray_view


class TrayMixin:
    tray = None
    tray_menu = None
    profiles = None

    def build_tray(self):
        self.tray_menu = TrayMenu(open_page=lambda page: self.open_page(None, page),
                                  open_window=lambda: self.open_page(None, None),
                                  open_settings=self.open_settings, quit_app=self.stop,
                                  switch_profile=self.switch_profile)
        self.tray = Tray(IndicatorFactory(self.tray_menu.menu), IconWriter(paths.ICONS))
        self.render_indicator()

    def render_indicator(self):
        if self.tray is None:
            return
        view = tray_view(self.current_power(), self.data["system"], self.data["slow"],
                         self.settings, self.preferences)
        self.tray.apply(view)
        self.tray_menu.set_lines(view.menu)
        self.tray_menu.batteries.sync(battery_menu_rows(self.data["devices"]))

    def hide_indicators(self):
        if self.tray is not None:
            self.tray.hide_all()

    def start_profiles(self):
        if self.preview:
            self.profiles = DemoProfiles(demo.preview_profile(self.preview), self.profiles_changed)
        else:
            self.profiles = PowerProfiles(self.collector.bus, self.profiles_changed)
        self.profiles.start()

    def profiles_changed(self, state):
        if self.tray_menu is not None:
            self.tray_menu.set_profiles(state)
        if self.power_page is not None:
            self.power_page.set_profiles(state)

    def switch_profile(self, name):
        def done(ok):
            if not ok and not self.closing:
                self.profiles_changed(self.profiles.state)
        if self.profiles is None:
            return
        self.profiles.set(name, done)

    def stop_profiles(self):
        if self.profiles is not None:
            self.profiles.stop()
