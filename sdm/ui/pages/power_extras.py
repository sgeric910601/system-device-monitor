"""「電源」頁下方兩種版面共用的卡片：電源模式與耗電排行。

- 電源模式：電源模式服務（power-profiles-daemon）回報的模式（省電／平衡／效能），
  點選後由看板非同步切換；失敗時恢復成實際的模式。沒有服務時整張卡片隱藏。
- 耗電排行：資料來自頁面顯示時的詳細取樣（只取各 App 的 CPU）；頁面沒有顯示時只記下最新資料。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.services.power_profiles import profile_title
from sdm.ui.monitor_widgets import TopList, card, set_text
from sdm.ui.ranking import power_ranking
from sdm.ui.widgets import box, discard, label

MODE_NOTE = "與 GNOME 快速設定裡的電源模式相同，由系統的電源模式服務切換。"


class PowerModeCard:
    """不是 GTK 元件本身，卡片外框在 container。"""

    def __init__(self, switch):
        self.switch = switch
        self.buttons, self.available = {}, None
        self.row = box(False, 0)
        self.row.get_style_context().add_class("linked")
        self.row.set_halign(Gtk.Align.START)
        self.container = card("電源模式", self.row, label(MODE_NOTE, "muted", True))
        self.container.show_all()
        self.container.set_no_show_all(True)
        self.container.hide()

    def set_state(self, state):
        if state.available != self.available:
            self.rebuild(state.available)
        for name, (button, handler) in self.buttons.items():
            if button.get_active() != (name == state.active):
                button.handler_block(handler)
                button.set_active(name == state.active)
                button.handler_unblock(handler)
        self.container.set_visible(bool(state.available))

    def rebuild(self, available):
        self.available = available
        discard(self.row, [button for button, _ in self.buttons.values()])
        self.buttons, group = {}, None
        for name in available:
            button = Gtk.RadioButton.new_with_label_from_widget(group, profile_title(name))
            button.set_mode(False)              # 按鈕外觀，不畫圓點
            group = group or button
            handler = button.connect("toggled", self.toggled, name)
            self.row.pack_start(button, False, False, 0)
            self.buttons[name] = (button, handler)
        self.row.show_all()

    def toggled(self, button, name):
        if button.get_active():
            self.switch(name)


class RankingCard:
    """不是 GTK 元件本身，卡片外框在 container。"""

    def __init__(self, names, cpu_watts=lambda: None):
        self.names, self.cpu_watts = names, cpu_watts
        self.apps = None
        self.top = TopList()
        self.note = label("", "muted", True)
        self.note.set_halign(Gtk.Align.START)
        self.container = card("耗電排行", self.top, self.note)

    def set_apps(self, apps):
        self.apps = apps

    def apply(self):
        rows, note, empty = power_ranking(self.apps, self.names, self.cpu_watts())
        self.top.set_rows(rows, empty)
        set_text(self.note, note)
