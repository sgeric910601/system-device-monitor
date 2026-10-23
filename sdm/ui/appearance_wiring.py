"""看板的外觀部分（Monitor 的混入類別）：程式圖示的顏色；夥伴的設定轉給 companion_wiring。

- 啟動時與「設定 → 外觀」換色時：所有視窗的預設圖示、頂端列沒有項目時的程式圖示立即換成
  選擇的顏色（直接讀 assets/app，不經過圖示主題）。
- 一般執行時另外讓使用者圖示主題與選擇一致（sdm.services.app_icon）；示範模式與模擬測試不寫檔。
- 寫不進去時記下原因（icon_problem），設定視窗會顯示。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from sdm.constants import AppIcon
from sdm.services import app_icon

LOG = logging.getLogger(__name__)
ICON_KEYS = frozenset({"app_icon_color"})
TRAY_ICON_SIZE = 128


def set_window_icons(color):
    """所有之後建立、沒有自己指定圖示的視窗都用這組圖示。"""
    try:
        icons = [GdkPixbuf.Pixbuf.new_from_file(str(app_icon.asset_path(color, size)))
                 for size in AppIcon.SIZES]
    except GLib.Error as error:
        LOG.warning("Cannot load the application icon: %s", error.message)
        return
    Gtk.Window.set_default_icon_list(icons)
    for window in Gtk.Window.list_toplevels():
        window.set_icon_list(icons)


def rescan_icon_theme():
    theme = Gtk.IconTheme.get_default()
    if theme is not None:
        theme.rescan_if_needed()


class AppearanceMixin:
    icon_problem = None

    def apply_app_icon(self):
        color = self.preferences.app_icon_color
        set_window_icons(color)
        if getattr(self, "tray", None) is not None:
            self.tray.set_app_icon(str(app_icon.asset_path(color, TRAY_ICON_SIZE)))
            self.render_indicator()
        if self.smoke or self.preview:
            return
        result = app_icon.apply_color(color)
        self.icon_problem = result.problem
        if result.changed:
            rescan_icon_theme()

    def appearance_changed(self, changed):
        """設定改變後呼叫（settings_wiring）：程式圖示，以及夥伴（companion_wiring）。"""
        if changed & ICON_KEYS:
            self.apply_app_icon()
        self.companion_settings_changed(changed)
