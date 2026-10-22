"""主視窗與設定視窗共用的頁面導覽：左側 Gtk.StackSidebar，視窗寬度小於 640 px 時
側欄收起，改用標題列的頁面選單按鈕（彈出清單）。

隱藏的頁面（例如關閉的系統監控模組）在側欄與彈出清單都不顯示；
寬度在 size-allocate 時檢查，狀態改變才在閒置時切換，避免在配置中途改版面。
窄視窗沒有側欄：標題列的副標題改顯示目前的頁面，視窗加上 narrow 樣式（頁面內距較小）。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from sdm.constants import Ui

MENU_ICON = "view-list-symbolic"
NARROW_CLASS = "narrow"


def is_narrow(width):
    return 0 < width < Ui.NARROW_WIDTH


class NavigationShell:
    def __init__(self, header, menu_tip="切換頁面"):
        self.header, self.subtitle, self.window = header, header.get_subtitle(), None
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.sidebar = Gtk.StackSidebar(stack=self.stack)
        self.sidebar.set_size_request(Ui.SIDEBAR_WIDTH, -1)
        self.separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        for child, expand in ((self.sidebar, False), (self.separator, False), (self.stack, True)):
            self.widget.pack_start(child, expand, expand, 0)
        for child in (self.sidebar, self.separator):
            child.show_all()
            child.set_no_show_all(True)     # 顯示與否只由寬度決定，不受 show_all() 影響
        self.menu_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.menu_list.set_margin_top(6)
        self.menu_list.set_margin_bottom(6)
        popover = Gtk.Popover()
        popover.add(self.menu_list)
        self.menu_button = Gtk.MenuButton(popover=popover)
        self.menu_button.remove(self.menu_button.get_child())     # 換掉預設的箭頭圖示
        image = Gtk.Image.new_from_icon_name(MENU_ICON, Gtk.IconSize.BUTTON)
        image.show()
        self.menu_button.add(image)
        self.menu_button.set_tooltip_text(menu_tip)
        self.menu_button.set_no_show_all(True)
        header.pack_start(self.menu_button)
        self.buttons, self.titles = {}, {}
        self.narrow, self.pending, self.width = None, None, 0
        self.stack.connect("notify::visible-child-name", self.update_subtitle)

    def add(self, key, title, child):
        self.stack.add_titled(child, key, title)
        self.titles[key] = title
        button = Gtk.ModelButton(text=title)
        button.connect("clicked", self.choose, key)
        button.set_no_show_all(True)
        button.show()
        self.menu_list.pack_start(button, False, False, 0)
        self.buttons[key] = button

    def choose(self, _button, key):
        self.stack.set_visible_child_name(key)
        self.menu_button.get_popover().popdown()

    def set_visible(self, key, visible):
        """隱藏的頁面不出現在側欄與彈出清單；正在顯示的頁面被隱藏時 GTK 會換到其他頁。"""
        child = self.stack.get_child_by_name(key)
        child.set_no_show_all(not visible)
        if visible:
            child.show_all()
        else:
            child.hide()
        self.buttons[key].set_visible(visible)

    def track(self, window):
        """依視窗寬度切換；視窗本身的預設寬度就小於 640 px 時一開始就收起側欄。"""
        self.window = window
        window.connect("size-allocate", self.on_allocate)
        self.width = window.get_default_size()[0]
        self.apply_narrow()

    def on_allocate(self, _widget, allocation):
        self.width = allocation.width
        if is_narrow(self.width) != self.narrow and self.pending is None:
            self.pending = GLib.idle_add(self.apply_narrow)

    def apply_narrow(self):
        """閒置時依最新的寬度切換（配置期間可能又變過）。"""
        self.pending = None
        narrow = is_narrow(self.width)
        self.narrow = narrow
        self.sidebar.set_visible(not narrow)
        self.separator.set_visible(not narrow)
        self.menu_button.set_visible(narrow)
        if self.window is not None:
            context = self.window.get_style_context()
            (context.add_class if narrow else context.remove_class)(NARROW_CLASS)
        self.update_subtitle()
        return False

    def update_subtitle(self, *_):
        key = self.stack.get_visible_child_name()
        title = self.titles.get(key) if self.narrow else None
        self.header.set_subtitle(title or self.subtitle)
