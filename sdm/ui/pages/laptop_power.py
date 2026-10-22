"""筆電模式的「電源」頁：電量標題、時間卡片、電力流向、今日回顧、電量歷史與詳細資訊。

所有文字都由 battery_text／battery_details 產生；這裡只負責排版，文字沒變就不重設。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from sdm.constants import Flow
from sdm.ui.battery_details import detail_rows
from sdm.ui.battery_flow import BatteryFlow
from sdm.ui.battery_text import (TONE_CRITICAL, TONE_WARNING, flow_model, header, recap_text,
                                 time_card)
from sdm.ui.charts import LEVEL_BATTERY, HistoryChart
from sdm.ui.widgets import box, clear, label

TONE_CLASSES = {TONE_WARNING: "power-warning", TONE_CRITICAL: "power-critical"}
SPACING = 16
CARD_PADDING = 16
DETAIL_COLUMNS = 2
DETAIL_FIELD_WIDTH = 260        # 每個欄位的最小寬度：放不下兩欄時改成一欄
DETAIL_NAME_WIDTH = 96          # 欄位名稱的寬度（「不斷電系統型號」也放得下），值對齊
RECAP_COLUMNS = 3
RECAP_NOTE = "依電量歷史計算（看板沒在執行的時段不列入）"


def set_text(widget, text):
    if widget.get_text() != text:
        widget.set_text(text)


def set_tone(widgets, tone):
    for widget in widgets:
        context = widget.get_style_context()
        for name in TONE_CLASSES.values():
            context.remove_class(name)
        if tone in TONE_CLASSES:
            context.add_class(TONE_CLASSES[tone])


def titled_card(title, *children):
    """卡片外框；title 為標籤元件，內容改變時可以直接改它的文字。"""
    container = box(spacing=10)
    container.get_style_context().add_class("card")
    title.get_style_context().add_class("section")
    container.pack_start(title, False, False, 0)
    for child in children:
        container.pack_start(child, False, False, 0)
    return container


def ellipsized(text, css=None):
    widget = label(text, css)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_hexpand(True)
    return widget


class LabelGrid(Gtk.Grid):
    """依列重建的表格：結構（列數與標題）改變才重建，否則只更新文字。"""

    def __init__(self, columns_css=()):
        super().__init__(column_spacing=SPACING, row_spacing=6)
        self.columns_css, self.keys, self.cells = columns_css, None, []

    def set_rows(self, rows, key=None):
        key = tuple(row[0] for row in rows) if key is None else key
        if key != self.keys:
            self.keys = key
            clear(self)
            self.cells = [self.build_row(index, row) for index, row in enumerate(rows)]
            # 表格本身可能設了不隨 show_all() 顯示，所以逐一顯示新建的儲存格
            for child in self.get_children():
                child.show()
        for cells, row in zip(self.cells, rows):
            for widget, text in zip(cells, row):
                set_text(widget, text)

    def build_row(self, index, row):
        cells = []
        for column, text in enumerate(row):
            css = self.columns_css[column] if column < len(self.columns_css) else None
            widget = label(text, css)
            if column:
                widget.set_xalign(1)
            widget.set_hexpand(column == 0)
            self.attach(widget, column, index, 1, 1)
            cells.append(widget)
        return cells


class TimeCardView:
    """時間卡片；不是 GTK 元件本身，卡片外框在 container。"""

    def __init__(self):
        self.title = label("預估可用時間")
        self.message = label("", "power-sub", True)
        self.table = LabelGrid(("muted", None, None))
        self.rows = LabelGrid(("muted", None))
        self.note = label("", "muted", True)
        self.container = titled_card(self.title, self.message, self.table, self.rows, self.note)
        for widget in (self.message, self.table, self.note):
            widget.set_no_show_all(True)

    def set_card(self, card):
        set_text(self.title, card.title)
        set_text(self.message, card.message)
        set_text(self.note, card.note)
        self.message.set_visible(bool(card.message))
        self.note.set_visible(bool(card.note))
        table = (card.columns,) + card.table if card.table else ()
        self.table.set_rows(table, key=(card.title, len(table)))
        self.table.set_visible(bool(table))
        self.rows.set_rows(card.rows)


class RecapView:
    def __init__(self):
        self.headline = label("", "muted", True)
        self.grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        self.metrics = []
        self.container = titled_card(label("今日電量回顧"), self.headline, self.grid,
                                     label(RECAP_NOTE, "muted", True))

    def set_text(self, text):
        set_text(self.headline, text.headline)
        if not self.metrics:
            for index, (title, value) in enumerate(text.metrics):
                metric = box(spacing=2)
                metric.get_style_context().add_class("metric")
                labels = (label(value, "metric-value"), label(title, "muted"))
                for widget in labels:
                    metric.pack_start(widget, False, False, 0)
                row, column = divmod(index, RECAP_COLUMNS)
                self.grid.attach(metric, column, row, 1, 1)
                self.metrics.append(labels)
            self.grid.show_all()
        for (value_label, title_label), (title, value) in zip(self.metrics, text.metrics):
            set_text(value_label, value)
            set_text(title_label, title)


class DetailsView:
    """詳細資訊：寬的時候兩欄，窄的時候（每欄不到 260 px）自動改成一欄。"""

    def __init__(self):
        self.grid = Gtk.FlowBox(homogeneous=True, min_children_per_line=1,
                                max_children_per_line=DETAIL_COLUMNS, column_spacing=SPACING,
                                row_spacing=8, selection_mode=Gtk.SelectionMode.NONE)
        self.ids, self.values = None, []
        self.empty = label("所有欄位都已隱藏。", "muted")
        self.empty.set_no_show_all(True)
        self.container = titled_card(label("詳細資訊"), self.grid, self.empty)

    def set_rows(self, rows):
        ids = [row[0] for row in rows]
        if ids != self.ids:
            self.ids = ids
            clear(self.grid)
            self.values = [self.add_field(name, value) for _, name, value in rows]
            for child in self.grid.get_children():
                child.set_can_focus(False)
            self.grid.show_all()
        self.empty.set_visible(not rows)
        self.update_values(rows)

    def add_field(self, name, value):
        field = box(False, SPACING)
        field.set_size_request(DETAIL_FIELD_WIDTH, -1)
        title = label(name, "muted")
        title.set_size_request(DETAIL_NAME_WIDTH, -1)
        widget = ellipsized(value)
        widget.set_tooltip_text(value)
        field.pack_start(title, False, False, 0)
        field.pack_start(widget, True, True, 0)
        self.grid.add(field)
        return widget

    def update_values(self, rows):
        for widget, (_, _, value) in zip(self.values, rows):
            if widget.get_text() != value:
                widget.set_text(value)
                widget.set_tooltip_text(value)


class LaptopPowerView(Gtk.Box):
    def __init__(self, points_for, clock):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=SPACING)
        self.points_for, self.clock = points_for, clock
        self.build_header()
        self.time, self.flow = TimeCardView(), BatteryFlow()
        self.recap, self.details = RecapView(), DetailsView()
        self.chart = HistoryChart(lambda _range: self.refresh_chart(), LEVEL_BATTERY)
        flow_card = titled_card(label("電力流向"), self.flow)
        # 不等高：每一列依該列最高的卡片決定高度，上方的卡片不會被拉成下方圖表的高度
        cards = Gtk.FlowBox(homogeneous=False, min_children_per_line=1, max_children_per_line=2,
                            column_spacing=SPACING, row_spacing=SPACING,
                            selection_mode=Gtk.SelectionMode.NONE)
        for child in (self.time.container, flow_card, self.recap.container,
                      titled_card(label("電量歷史"), self.chart)):
            child.set_size_request(Flow.MIN_WIDTH + 2 * CARD_PADDING, -1)
            cards.add(child)
        for child in cards.get_children():
            child.set_can_focus(False)
        self.pack_start(cards, False, False, 0)
        self.pack_start(self.details.container, False, False, 0)

    def build_header(self):
        header_box = box(spacing=2)
        self.value = label("—", "power-value")
        self.state = label("正在讀取電池…", "power-state")
        self.headline = label("", "power-headline", True)
        for widget in (self.value, self.state, self.headline):
            header_box.pack_start(widget, False, False, 0)
        self.pack_start(header_box, False, False, 0)

    def apply(self, power, now, unplugged_at, recap, hidden):
        big, state, headline, tone = header(power)
        set_text(self.value, big)
        set_text(self.state, state)
        set_text(self.headline, headline)
        set_tone((self.value, self.state, self.headline), tone)
        self.time.set_card(time_card(power, now, unplugged_at))
        self.flow.set_model(flow_model(power))
        self.recap.set_text(recap_text(recap))
        self.details.set_rows(detail_rows(power, hidden))

    def refresh_chart(self):
        now = self.clock()
        self.chart.set_points(self.points_for(self.chart.range_s, now), now)
