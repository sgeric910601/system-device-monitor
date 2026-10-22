"""系統監控頁的共同基底：頁面沒有顯示時只記下最新資料，顯示時才更新文字與重畫。

每頁自成一個 Gtk.Box，對外只有 set_system(fast, slow) 與 set_detail(snapshot)，
之後換外殼（側欄）時可直接重用。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.constants import Flow
from sdm.ui.monitor_widgets import badge
from sdm.ui.widgets import box, label

SPACING = 16
CARD_PADDING = 16                   # 與 CSS .card 的 padding 相同
CARD_MIN_WIDTH = Flow.MIN_WIDTH + 2 * CARD_PADDING


def card_flow(*cards, columns=2):
    """卡片排成最多 columns 欄，視窗變窄時自動改成一欄。"""
    flow = Gtk.FlowBox(homogeneous=True, min_children_per_line=1,
                       max_children_per_line=columns, column_spacing=SPACING,
                       row_spacing=SPACING, selection_mode=Gtk.SelectionMode.NONE)
    flow.set_valign(Gtk.Align.START)
    for child in cards:
        child.set_size_request(CARD_MIN_WIDTH, -1)
        flow.add(child)
    for child in flow.get_children():
        child.set_can_focus(False)
    return flow


class Header(Gtk.Box):
    """頁首：小標題、大數字、說明，以及選用的徽章。"""

    def __init__(self, title, with_badge=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title = label(title, "muted", True)
        self.value = label("—", "monitor-value")
        self.sub = label("讀取中…", "power-sub", True)
        for widget in (self.title, self.value, self.sub):
            self.pack_start(widget, False, False, 0)
        self.badge = badge() if with_badge else None
        if self.badge is not None:
            self.badge.set_margin_top(4)
            self.pack_start(self.badge, False, False, 0)


class MonitorPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=SPACING)
        self.stale = True
        self.connect("map", self.on_map)

    def changed(self):
        """收到新資料：顯示中立即套用，否則等下次顯示。"""
        self.stale = True
        if self.get_mapped():
            self.refresh()

    def on_map(self, *_):
        if self.stale:
            self.refresh()

    def refresh(self):
        self.stale = False
        self.apply()

    def apply(self):
        raise NotImplementedError

    def set_system(self, fast, slow):
        """頂端列那一層的資料（2 秒與 5 秒）；需要的頁面覆寫。"""

    def set_detail(self, snapshot):
        """頁面顯示時的詳細取樣；需要的頁面覆寫。"""

    def seed(self, histories):
        """示範模式：預先填好的曲線（鍵見各頁）；需要的頁面覆寫。"""


def note(text):
    widget = label(text, "muted", True)
    widget.set_halign(Gtk.Align.START)
    return widget


def section_box(spacing=8):
    return box(spacing=spacing)
