"""「電源」頁：桌機顯示整機耗電、電力流向、耗電拆解、耗電歷史與詳細資訊；
有系統電池（筆電）時改顯示筆電版面（laptop_power.LaptopPowerView，第一次收到筆電快照才建立）。

自成一個 Gtk.Box，對外只有 update(power) 與 history_changed()，之後換外殼可直接重用。
頁面沒有顯示時只記下最新資料，顯示時才更新文字與重算圖表；文字沒變就不重設。
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from sdm.constants import Flow
from sdm.core.recap import DailyRecap
from sdm.services.battery_service import LaptopPower
from sdm.ui.charts import HistoryChart
from sdm.ui.pages.laptop_power import DetailsView, LaptopPowerView
from sdm.ui.power_flow import PowerFlow
from sdm.ui.power_text import (TONE_CRITICAL, TONE_WARNING, breakdown_rows, detail_rows,
                               flow_model, header_text)
from sdm.ui.widgets import box, clear, label

TONE_CLASSES = {TONE_WARNING: "power-warning", TONE_CRITICAL: "power-critical"}
SPACING = 16
CARD_PADDING = 16     # 與 CSS .card 的 padding 相同


def set_text(widget, text):
    if widget.get_text() != text:
        widget.set_text(text)


def card(title, *children):
    container = box(spacing=10)
    container.get_style_context().add_class("card")
    container.pack_start(label(title, "section"), False, False, 0)
    for child in children:
        container.pack_start(child, False, False, 0)
    return container


def ellipsized(text, css=None):
    widget = label(text, css)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_hexpand(True)
    return widget


class PowerPage(Gtk.Box):
    def __init__(self, points_for, clock=time.time, recap_for=lambda now: DailyRecap(),
                 unplugged_for=lambda now: None, hidden_fields=lambda: (),
                 hidden_flow=lambda: ()):
        """points_for(range_s, now) 回傳歷史圖表點；recap_for(now) 今日回顧與
        unplugged_for(now) 由歷史推算的拔電時間只在筆電模式使用；
        hidden_fields()、hidden_flow() 為使用者隱藏的詳細資訊欄位與電力流向細項。"""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=SPACING)
        self.points_for, self.clock = points_for, clock
        self.recap_for, self.unplugged_for = recap_for, unplugged_for
        self.hidden_fields, self.hidden_flow = hidden_fields, hidden_flow
        self.power, self.stale = None, True
        self.row_ids, self.row_labels = None, []
        self.laptop = None
        self.desktop = box(spacing=SPACING)
        self.pack_start(self.desktop, False, False, 0)
        self.build_header()
        self.build_cards()
        self.build_details()
        # 兩種版面的顯示與隱藏由這裡控制：之後視窗的 show_all() 不會把隱藏的版面又顯示出來
        self.desktop.show_all()
        self.desktop.set_no_show_all(True)
        self.connect("map", self.on_map)

    def build_header(self):
        header = box(spacing=2)
        self.value = label("—", "power-value")
        self.sub = label("正在讀取耗電…", "power-sub", True)
        header.pack_start(self.value, False, False, 0)
        header.pack_start(self.sub, False, False, 0)
        self.desktop.pack_start(header, False, False, 0)

    def build_cards(self):
        self.flow = PowerFlow()
        self.rows = Gtk.Grid(column_spacing=SPACING, row_spacing=6)
        self.message = label("", "muted", True)
        self.chart = HistoryChart(lambda _range: self.refresh_chart())
        cards = Gtk.FlowBox(homogeneous=True, min_children_per_line=1, max_children_per_line=2,
                            column_spacing=SPACING, row_spacing=SPACING,
                            selection_mode=Gtk.SelectionMode.NONE)
        for child in (card("耗電流向", self.flow, self.rows, self.message),
                      card("耗電歷史", self.chart)):
            child.set_size_request(Flow.MIN_WIDTH + 2 * CARD_PADDING, -1)
            cards.add(child)
        for child in cards.get_children():
            child.set_can_focus(False)
        self.desktop.pack_start(cards, False, False, 0)

    def build_details(self):
        self.details = DetailsView()
        self.details.set_rows(detail_rows(None, self.hidden_fields()))
        self.desktop.pack_start(self.details.container, False, False, 0)

    def update(self, power):
        """收到新的 DesktopPower、LaptopPower 或 None；同一份快照不重複處理。"""
        if power is self.power and not self.stale:
            return
        self.power, self.stale = power, True
        self.select_view(isinstance(power, LaptopPower))
        if self.get_mapped():
            self.apply()

    def select_view(self, laptop):
        """第一次收到筆電快照才建立筆電版面；之後依快照種類切換。"""
        if laptop and self.laptop is None:
            self.laptop = LaptopPowerView(self.points_for, self.clock)
            self.pack_start(self.laptop, False, False, 0)
            self.laptop.show_all()
            self.laptop.set_no_show_all(True)
            if self.get_mapped():
                self.laptop.refresh_chart()
        if self.laptop is not None:
            self.laptop.set_visible(laptop)
            self.desktop.set_visible(not laptop)

    def settings_changed(self):
        """隱藏的欄位或細項改變：顯示中立即重畫，否則等下次顯示。"""
        self.stale = True
        if self.get_mapped():
            self.apply()

    def history_changed(self):
        if self.get_mapped():
            self.refresh_chart()

    def on_map(self, *_):
        if self.stale:
            self.apply()
        self.refresh_chart()

    def apply(self):
        self.stale = False
        power = self.power
        if isinstance(power, LaptopPower):
            now = self.clock()
            self.laptop.apply(power, now, self.unplugged_for(now), self.recap_for(now),
                              self.hidden_fields())
            return
        self.apply_desktop(power)

    def apply_desktop(self, power):
        big, sub, tone = header_text(power)
        set_text(self.value, big)
        set_text(self.sub, sub)
        for widget in (self.value, self.sub):
            context = widget.get_style_context()
            for name in TONE_CLASSES.values():
                context.remove_class(name)
            if tone in TONE_CLASSES:
                context.add_class(TONE_CLASSES[tone])
        hidden = self.hidden_flow()
        self.flow.set_model(flow_model(power, hidden))
        self.update_rows(power.breakdown if power else None, hidden)
        self.details.set_rows(detail_rows(power, self.hidden_fields()))

    def update_rows(self, breakdown, hidden=()):
        """拆解清單：項目組成改變才重建，否則只更新文字。"""
        rows = breakdown_rows(breakdown, hidden) if breakdown else ()
        set_text(self.message, breakdown.message if breakdown else "")
        ids = [row[0] for row in rows]
        if ids != self.row_ids:
            self.row_ids = ids
            clear(self.rows)
            self.row_labels = []
            for index, (_, name, watts, share) in enumerate(rows):
                labels = (ellipsized(name), label(watts), label(share, "muted"))
                for column, widget in enumerate(labels):
                    if column:
                        widget.set_xalign(1)
                    self.rows.attach(widget, column, index, 1, 1)
                self.row_labels.append(labels)
            self.rows.show_all()
        for labels, row in zip(self.row_labels, rows):
            for widget, text in zip(labels, row[1:]):
                set_text(widget, text)

    def refresh_chart(self):
        if self.laptop is not None and self.laptop.get_visible():
            self.laptop.refresh_chart()
            return
        now = self.clock()
        self.chart.set_points(self.points_for(self.chart.range_s, now), now)
