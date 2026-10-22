"""系統監控頁共用的 GTK 元件：2 分鐘曲線、組成長條、各核心長條、表格與 App 排行。

座標與分段在 sdm.core.monitor_history 算好，這裡只負責畫；文字沒變就不重設。
"""
import math
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, Gtk, Pango, PangoCairo

from sdm.constants import Monitor
from sdm.core import byte_format
from sdm.core.chart_layout import linear
from sdm.core.monitor_history import MonitorHistory, axis_max, runs
from sdm.ui.theme import rgb
from sdm.ui.widgets import box, clear, label

TONES = ("good", "warning", "critical")
CHART_HEIGHT = 92
AXIS_WIDTH = 44                 # 右側 Y 軸刻度
CHART_PADDING = 6
LINE_WIDTH = 1.5
AREA_ALPHA = 0.18
GRID_ALPHA = 0.14
LABEL_ALPHA = 0.7
CORE_HEIGHT = 48
CORE_GAP = 3
CORE_MIN_WIDTH = 3
TRACK_ALPHA = 0.10
BAR_HEIGHT = 14
ICON_SIZE = 20
SPACING = 8


def set_text(widget, text):
    if widget.get_text() != text:
        widget.set_text(text)


def set_tone(widget, tone, prefix="tone-"):
    """移除舊的色調類別，加上新的（normal 不加）。"""
    context = widget.get_style_context()
    for name in TONES:
        context.remove_class(prefix + name)
    if tone in TONES:
        context.add_class(prefix + tone)


def ellipsized(text="", css=None):
    widget = label(text, css)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_hexpand(True)
    return widget


def card(title, *children):
    """卡片外框；title 為文字或標籤元件。"""
    container = box(spacing=10)
    container.get_style_context().add_class("card")
    heading = label(title, "section") if isinstance(title, str) else title
    container.pack_start(heading, False, False, 0)
    for child in children:
        container.pack_start(child, False, False, 0)
    return container


def badge(text=""):
    widget = label(text, "badge")
    widget.set_halign(Gtk.Align.START)
    return widget


def legend(entries):
    """「● CPU　● GPU」這類圖例；entries 為（顏色、文字）。"""
    text = "　".join(f'<span foreground="{color}">●</span> {name}' for color, name in entries)
    return label(text, "muted", markup=True)


def foreground(widget):
    color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
    return color.red, color.green, color.blue


class MonitorChart(Gtk.DrawingArea):
    """最近 2 分鐘的一或兩條線；percent 為 True 時 Y 軸固定 0～100%。"""

    def __init__(self, color_a, color_b=None, percent=True, clock=time.monotonic):
        super().__init__()
        self.color_a = rgb(color_a)
        self.color_b = rgb(color_b) if color_b else None
        self.percent, self.clock = percent, clock
        self.history = MonitorHistory()
        self.set_size_request(-1, CHART_HEIGHT)
        self.set_hexpand(True)
        self.connect("draw", self.on_draw)

    def set_history(self, history):
        self.history = history
        self.queue_draw()

    def frame(self):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        return CHART_PADDING, width - AXIS_WIDTH, CHART_PADDING, height - CHART_PADDING

    def on_draw(self, _widget, cr):
        left, right, top, bottom = self.frame()
        if right <= left or bottom <= top:
            return False
        top_value = axis_max(self.history, self.percent)
        self.draw_grid(cr, (left, right, top, bottom), top_value)
        end = self.clock()
        start = end - Monitor.HISTORY_WINDOW_SECONDS
        cr.save()
        cr.rectangle(left, top, right - left, bottom - top)
        cr.clip()
        geometry = (left, right, top, bottom, start, end, top_value)
        self.draw_line(cr, runs(self.history, "a"), geometry, self.color_a, True)
        if self.color_b is not None:
            self.draw_line(cr, runs(self.history, "b"), geometry, self.color_b, False)
        cr.restore()
        return False

    def axis_text(self, value):
        return f"{value:.0f}%" if self.percent else byte_format.axis_rate(value)

    def draw_grid(self, cr, frame, top_value):
        left, right, top, bottom = frame
        fg = foreground(self)
        layout = self.create_pango_layout("")
        cr.set_line_width(1)
        for fraction in (0.0, 0.5, 1.0):
            y = round(linear(fraction, 0, 1, bottom, top)) + 0.5
            cr.set_source_rgba(*fg, GRID_ALPHA)
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
            layout.set_text(self.axis_text(top_value * fraction), -1)
            _, height = layout.get_pixel_size()
            cr.set_source_rgba(*fg, LABEL_ALPHA)
            cr.move_to(right + CHART_PADDING, min(max(y - height / 2, 0),
                                                  self.get_allocated_height() - height))
            PangoCairo.show_layout(cr, layout)

    @staticmethod
    def draw_line(cr, line_runs, geometry, color, filled):
        left, right, top, bottom, start, end, top_value = geometry
        cr.set_line_width(LINE_WIDTH)
        for run in line_runs:
            points = [(linear(t, start, end, left, right),
                       linear(min(value, top_value), 0, top_value, bottom, top))
                      for t, value in run]
            if len(points) == 1:
                cr.set_source_rgba(*color, 1)
                cr.arc(points[0][0], points[0][1], LINE_WIDTH * 1.5, 0, 2 * math.pi)
                cr.fill()
                continue
            if filled:
                cr.set_source_rgba(*color, AREA_ALPHA)
                cr.move_to(points[0][0], bottom)
                for x, y in points:
                    cr.line_to(x, y)
                cr.line_to(points[-1][0], bottom)
                cr.close_path()
                cr.fill()
            cr.set_source_rgba(*color, 1)
            cr.move_to(*points[0])
            for x, y in points[1:]:
                cr.line_to(x, y)
            cr.stroke()


class CompositionBar(Gtk.DrawingArea):
    """堆疊長條：segments 為（比例、RGBA），依序由左到右。"""

    def __init__(self):
        super().__init__()
        self.segments = ()
        self.set_size_request(-1, BAR_HEIGHT)
        self.set_hexpand(True)
        self.connect("draw", self.on_draw)

    def set_segments(self, segments):
        if tuple(segments) != self.segments:
            self.segments = tuple(segments)
            self.queue_draw()

    def on_draw(self, _widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        x = 0.0
        for fraction, color in self.segments:
            span = max(0.0, min(1.0, fraction)) * width
            cr.set_source_rgba(*color)
            cr.rectangle(x, 0, max(0.0, span - 1), height)
            cr.fill()
            x += span
        return False


class CoreBars(Gtk.DrawingArea):
    """各核心使用率的直條（0～1），寬度依核心數平均分配。"""

    def __init__(self, color):
        super().__init__()
        self.values, self.color = (), rgb(color)
        self.set_size_request(-1, CORE_HEIGHT)
        self.set_hexpand(True)
        self.connect("draw", self.on_draw)

    def set_values(self, values):
        self.values = tuple(values)
        self.queue_draw()

    def on_draw(self, _widget, cr):
        count = len(self.values)
        if not count:
            return False
        width, height = self.get_allocated_width(), self.get_allocated_height()
        bar = max(CORE_MIN_WIDTH, (width - CORE_GAP * (count - 1)) / count)
        fg = foreground(self)
        for index, value in enumerate(self.values):
            x = index * (bar + CORE_GAP)
            cr.set_source_rgba(*fg, TRACK_ALPHA)
            cr.rectangle(x, 0, bar, height)
            cr.fill()
            filled = max(1.0, min(1.0, value) * height)
            cr.set_source_rgba(*self.color, 1)
            cr.rectangle(x, height - filled, bar, filled)
            cr.fill()
        return False


class RowsGrid(Gtk.Grid):
    """（名稱、值[、色調]）的表格：名稱改變才重建，否則只更新文字與色調。"""

    def __init__(self):
        super().__init__(column_spacing=16, row_spacing=6)
        self.names, self.cells = None, []

    def set_rows(self, rows):
        names = tuple(row[0] for row in rows)
        if names != self.names:
            self.names = names
            clear(self)
            self.cells = []
            for index, row in enumerate(rows):
                name, value = label(row[0], "muted"), ellipsized()
                self.attach(name, 0, index, 1, 1)
                self.attach(value, 1, index, 1, 1)
                self.cells.append(value)
            self.show_all()
        for widget, row in zip(self.cells, rows):
            set_text(widget, row[1])
            widget.set_tooltip_text(row[1])
            set_tone(widget, row[2] if len(row) > 2 else None)


def app_icon(icon):
    """圖示名稱或 Gio.Icon → 小圖示元件。"""
    if isinstance(icon, Gio.Icon):
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.LARGE_TOOLBAR)
    else:
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    image.set_pixel_size(ICON_SIZE)
    return image


class TopList(Gtk.Box):
    """App 排行：每列圖示、名稱、數值與次要文字；沒有資料時顯示 placeholder。"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=SPACING)
        self.message = label("計算中…", "muted")
        self.rows_box = box(spacing=SPACING)
        self.pack_start(self.message, False, False, 0)
        self.pack_start(self.rows_box, False, False, 0)
        self.keys, self.cells = None, []

    def set_message(self, text):
        set_text(self.message, text)
        self.message.set_visible(bool(text))

    def set_rows(self, rows, empty_text):
        """rows 為（名稱、圖示、數值、次要文字）。"""
        self.set_message("" if rows else empty_text)
        keys = tuple((row[0], str(row[1])) for row in rows)
        if keys != self.keys:
            self.keys = keys
            clear(self.rows_box)
            self.cells = [self.build_row(row) for row in rows]
            self.rows_box.show_all()
        for (value, secondary), row in zip(self.cells, rows):
            set_text(value, row[2])
            set_text(secondary, row[3])

    def build_row(self, row):
        line = box(False, SPACING)
        line.pack_start(app_icon(row[1]), False, False, 0)
        line.pack_start(ellipsized(row[0]), True, True, 0)
        secondary, value = label("", "muted"), label("", "metric-value")
        line.pack_end(value, False, False, 0)
        line.pack_end(secondary, False, False, 0)
        self.rows_box.pack_start(line, False, False, 0)
        return value, secondary
