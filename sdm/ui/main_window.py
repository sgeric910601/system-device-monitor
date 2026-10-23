"""主視窗外殼：標題列（頁面選單、設定、重新整理）、左側頁面側欄與頁尾。

頁面本身由各頁模組建立；這裡只負責外框、每頁的捲動容器與頁面顯示與否。
寬度小於 640 px 時側欄收起、頁面內距縮小，副標題顯示目前的頁面（sdm.ui.navigation）。
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from sdm.constants import Ui
from sdm.ui.navigation import NavigationShell
from sdm.ui.theme import css_data
from sdm.ui.widgets import box, label

TITLE = "系統與裝置監看"
HEADER_TITLE = "系統與裝置"
HEADER_SUBTITLE = "本機即時監看"
PAGES = (("power", "電源"), ("overview", "系統總覽"), ("cpu", "CPU／GPU"),
         ("memory", "記憶體"), ("disk", "磁碟"), ("network", "網路"), ("devices", "連線設備"))
SETTINGS_ICON = "emblem-system-symbolic"
REFRESH_ICON = "view-refresh-symbolic"
_css_installed = []


def install_css():
    """整個畫面共用的樣式只載入一次（主視窗與設定視窗都會用到）。"""
    if _css_installed:
        return
    css = Gtk.CssProvider()
    css.load_from_data(css_data())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed.append(css)


def icon_button(icon, tip, callback):
    button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    button.set_tooltip_text(tip)
    button.connect("clicked", lambda *_: callback())
    return button


class MainWindow:
    def __init__(self, application, on_refresh, on_settings, on_delete):
        install_css()
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_title(TITLE)
        self.window.set_default_size(Ui.WINDOW_WIDTH, Ui.WINDOW_HEIGHT)
        self.window.connect("delete-event", on_delete)
        self.header = Gtk.HeaderBar(title=HEADER_TITLE, subtitle=HEADER_SUBTITLE)
        self.header.set_show_close_button(True)
        self.window.set_titlebar(self.header)
        self.header.pack_end(icon_button(REFRESH_ICON, "更新所有資料", on_refresh))
        self.header.pack_end(icon_button(SETTINGS_ICON, "設定", on_settings))
        self.shell = NavigationShell(self.header)
        self.shell.track(self.window)
        root = box(spacing=0)
        root.pack_start(self.shell.widget, True, True, 0)
        self.footer = label("正在讀取本機資料…", "footnote")
        self.footer.set_line_wrap(True)
        root.pack_end(self.footer, False, False, 0)
        self.window.add(root)
        self.scrolls = {}

    @property
    def stack(self):
        return self.shell.stack

    def page(self, key, title):
        """新增一頁（含垂直捲動），回傳放內容的容器。"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = box(spacing=16)
        content.get_style_context().add_class("page")
        scroll.add(content)
        self.shell.add(key, title, scroll)
        self.scrolls[key] = scroll
        return content

    def set_page_visible(self, key, visible):
        self.shell.set_visible(key, visible)

    def show_page(self, key):
        if key in self.scrolls and self.scrolls[key].get_visible():
            self.stack.set_visible_child_name(key)

    def current_page(self):
        return self.stack.get_visible_child_name()
