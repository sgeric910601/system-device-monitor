"""連線設備頁每台回報電量的設備右邊的鈴鐺（參考專案 PeripheralsView 的鈴鐺選單）。

點一下打開選單：「不通知」或「低於 N% 時通知」；圖示為鈴鐺（開啟）或劃掉的鈴鐺（關閉），
提示文字寫出目前的門檻。設定檔唯讀時停用，提示文字說明原因；寫入被拒絕時提示文字寫出原因。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.peripheral_alerts import BELL_CHOICES, bell_icon, bell_tooltip
from sdm.services.settings_store import READ_ONLY

MENU_TITLE = "低電量通知"


class BellButton:
    def __init__(self, device_id, choose):
        self.device_id, self.choose = device_id, choose     # choose(識別碼, 門檻)
        self.button = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        self.button.set_relief(Gtk.ReliefStyle.NONE)
        self.image = Gtk.Image()
        self.button.add(self.image)
        self.button.get_accessible().set_name(MENU_TITLE)
        self.menu, self.items = self.build_menu()
        self.button.set_popup(self.menu)

    def build_menu(self):
        menu, items, group = Gtk.Menu(), [], None
        title = Gtk.MenuItem(label=MENU_TITLE)
        title.set_sensitive(False)
        menu.append(title)
        menu.append(Gtk.SeparatorMenuItem())
        for value, label in BELL_CHOICES:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or item
            handler = item.connect("toggled", self.toggled, value)
            menu.append(item)
            items.append((item, handler, value))
        menu.show_all()
        return menu, items

    def toggled(self, item, value):
        if item.get_active():
            self.choose(self.device_id, value)

    def sync(self, threshold, writable, problem=""):
        self.image.set_from_icon_name(bell_icon(threshold), Gtk.IconSize.BUTTON)
        tooltip = bell_tooltip(threshold) if writable else READ_ONLY
        self.button.set_tooltip_text(f"{problem}\n{tooltip}" if problem else tooltip)
        self.button.set_sensitive(writable)
        for item, handler, value in self.items:
            if item.get_active() != (value == threshold):
                item.handler_block(handler)
                item.set_active(value == threshold)
                item.handler_unblock(handler)

    def menu_open(self):
        return self.button.get_active()

    def destroy(self):
        """重建列表前銷毀選單（連同訊號），不留在記憶體裡。"""
        self.button.set_popup(None)
        self.menu.destroy()
