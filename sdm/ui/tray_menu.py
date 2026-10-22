"""頂端列共用的選單：每個項目一行（點一下開啟它的頁面）、設備電量、電源模式、
開啟看板、設定…、退出。

- 項目行依 tray_text.menu_lines() 顯示；關閉的監控模組不列出，文字沒變就不重設。
- 「電源模式」子選單只列出電源模式服務回報的模式；沒有服務時整個子選單隱藏。
  選了之後交給看板非同步切換，失敗時由看板把選項恢復成實際的模式。
- 設備電量每台一行，只有設備組成改變時才重建（BatteryRows，不依賴 GTK 以便測試）。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.services.power_profiles import profile_title

MENU_INDENT = "    "
LINE_KEYS = ("power", "cpu", "temperature", "ram", "gpu", "network", "ups")


class BatteryRows:
    def __init__(self, menu, header, make_item, on_activate):
        self.menu, self.header = menu, header
        self.make_item, self.on_activate = make_item, on_activate
        self.items, self.rows = [], None

    def sync(self, rows):
        """在標題下方每台設備一行；設備組成相同時只更新文字。"""
        if self.rows is not None and [k for k, _ in rows] == [k for k, _ in self.rows]:
            for item, (_, text) in zip(self.items, rows):
                if item.get_label() != MENU_INDENT + text:
                    item.set_label(MENU_INDENT + text)
        else:
            self.rebuild(rows)
        self.rows = rows

    def rebuild(self, rows):
        for item in self.items:
            self.menu.remove(item)
            item.destroy()
        start = self.menu.get_children().index(self.header) + 1
        self.items = []
        for offset, (key, text) in enumerate(rows):
            item = self.make_item(MENU_INDENT + text)
            item.set_sensitive(key is not None)
            if key is not None:
                item.connect("activate", self.on_activate)
            item.show()
            self.menu.insert(item, start + offset)
            self.items.append(item)


class TrayMenu:
    def __init__(self, open_page, open_window, open_settings, quit_app, switch_profile):
        self.open_page, self.switch_profile = open_page, switch_profile
        self.menu = Gtk.Menu()
        self.pages, self.lines, self.profiles = {}, {}, {}
        for key in LINE_KEYS:
            item = Gtk.MenuItem(label="")
            item.connect("activate", lambda _item, key=key: open_page(self.pages.get(key, "power")))
            item.set_no_show_all(True)
            self.menu.append(item)
            self.lines[key] = item
        header = Gtk.MenuItem(label="設備電量")
        header.connect("activate", lambda *_: open_page("devices"))
        self.menu.append(header)
        self.batteries = BatteryRows(self.menu, header, lambda text: Gtk.MenuItem(label=text),
                                     lambda *_: open_page("devices"))
        self.menu.append(Gtk.SeparatorMenuItem())
        self.mode_item, self.mode_menu = Gtk.MenuItem(label="電源模式"), Gtk.Menu()
        self.mode_item.set_submenu(self.mode_menu)
        self.mode_item.set_no_show_all(True)
        self.menu.append(self.mode_item)
        for title, action in (("開啟看板", open_window), ("設定…", open_settings),
                              ("退出", quit_app)):
            item = Gtk.MenuItem(label=title)
            item.connect("activate", lambda _item, action=action: action())
            self.menu.append(item)
        self.menu.show_all()

    def set_lines(self, lines):
        shown = set()
        for key, text, page in lines:
            item = self.lines[key]
            shown.add(key)
            self.pages[key] = page
            if item.get_label() != text:
                item.set_label(text)
        for key, item in self.lines.items():
            if item.get_visible() != (key in shown):
                item.set_visible(key in shown)

    def set_profiles(self, state):
        """依服務回報的模式重建子選單，並勾選目前的模式。"""
        if tuple(self.profiles) != state.available:
            self.rebuild_profiles(state.available)
        for name, (item, handler) in self.profiles.items():
            if item.get_active() != (name == state.active):
                item.handler_block(handler)
                item.set_active(name == state.active)
                item.handler_unblock(handler)
        self.mode_item.set_visible(bool(state.available))

    def rebuild_profiles(self, available):
        for item, _ in self.profiles.values():
            self.mode_menu.remove(item)
            item.destroy()
        self.profiles, group = {}, None
        for name in available:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, profile_title(name))
            group = group or item
            handler = item.connect("toggled", self.profile_toggled, name)
            self.mode_menu.append(item)
            self.profiles[name] = (item, handler)
        self.mode_menu.show_all()

    def profile_toggled(self, item, name):
        if item.get_active():
            self.switch_profile(name)
