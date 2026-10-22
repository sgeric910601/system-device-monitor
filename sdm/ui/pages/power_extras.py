"""「電源」頁下方兩種版面共用的卡片：耗電排行。

排行資料來自頁面顯示時的詳細取樣（只取各 App 的 CPU）；頁面沒有顯示時只記下最新資料。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.ui.monitor_widgets import TopList, card, set_text
from sdm.ui.ranking import power_ranking
from sdm.ui.widgets import label


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
