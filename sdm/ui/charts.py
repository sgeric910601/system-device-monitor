"""耗電歷史圖：1 小時／24 小時／7 天，耗電曲線（左軸）與不斷電系統電量（右軸）。

座標與刻度在 set_points() 算好，繪圖時只畫線與文字。
耗電曲線依 watts_segment 斷開；電量曲線依 segment 斷開，接電與改用電池分色。
"""
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, PangoCairo

from sdm.constants import Chart, History
from sdm.core.chart_layout import (axis_ticks, linear, nice_axis_max, ups_segments,
                                   watts_segments, watts_summary)
from sdm.ui.theme import COLORS, WARNING, rgb
from sdm.ui.widgets import box, label

RANGE_LABELS = ("1 小時", "24 小時", "7 天")
EMPTY_TEXT = "累積資料中，每分鐘記錄一次"
WATTS_COLOR = rgb(COLORS["temperature"])
UPS_COLOR = rgb(COLORS["ups"])
UPS_BATTERY_COLOR = rgb(WARNING)
GRID_ALPHA = 0.14
LABEL_ALPHA = 0.7
LABEL_GAP = 6
MIN_WATT_POINTS = 2              # 至少兩點才畫得出線（參考專案相同）
DOT_RADIUS = 2.5                 # 前後都斷開的單獨一點畫成圓點


def legend(color, text):
    return label(f'<span foreground="{color}">●</span> {text}', "muted", markup=True)


class HistoryChart(Gtk.Box):
    def __init__(self, on_range):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.on_range = on_range
        self.range_s = History.RANGES[0]
        self.points, self.ticks, self.axis_max, self.has_ups = (), (), 0.0, False
        self.watts_runs, self.ups_runs = [], []
        self.start = self.end = 0.0
        self.pack_start(self.build_toolbar(), False, False, 0)
        self.stack = Gtk.Stack()
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(-1, Chart.HEIGHT)
        self.area.connect("draw", self.on_draw)
        self.stack.add_named(self.area, "chart")
        empty = label(EMPTY_TEXT, "muted")
        empty.set_xalign(0.5)
        empty.set_size_request(-1, Chart.HEIGHT)
        self.stack.add_named(empty, "empty")
        self.pack_start(self.stack, False, False, 0)
        self.summary = label("", "muted")
        self.pack_start(self.summary, False, False, 0)

    def build_toolbar(self):
        toolbar = box(False, 12)
        buttons = box(False, 0)
        buttons.get_style_context().add_class("linked")
        group = None
        for range_s, text in zip(History.RANGES, RANGE_LABELS):
            button = Gtk.RadioButton.new_with_label_from_widget(group, text)
            button.set_mode(False)
            button.connect("toggled", self.range_toggled, range_s)
            buttons.pack_start(button, False, False, 0)
            group = group or button
        toolbar.pack_start(buttons, False, False, 0)
        toolbar.pack_end(legend(COLORS["ups"], "不斷電系統電量"), False, False, 0)
        toolbar.pack_end(legend(COLORS["temperature"], "耗電"), False, False, 0)
        return toolbar

    def range_toggled(self, button, range_s):
        if button.get_active() and range_s != self.range_s:
            self.range_s = range_s
            self.on_range(range_s)

    def set_points(self, points, now):
        """換上新的圖表點並重畫；耗電點少於兩個時顯示「累積資料中」。"""
        self.points = tuple(points)
        self.start, self.end = now - self.range_s, now
        watts = [point.watts for point in self.points if point.watts is not None]
        self.axis_max = nice_axis_max(max(watts, default=0), Chart.WATTS_AXIS_MINIMUM)
        self.ticks = axis_ticks(now, self.range_s)
        self.has_ups = any(point.ups_pct is not None for point in self.points)
        self.watts_runs = watts_segments(self.points)
        self.ups_runs = ups_segments(self.points)
        enough = len(watts) >= MIN_WATT_POINTS
        self.stack.set_visible_child_name("chart" if enough else "empty")
        self.summary.set_text((watts_summary(self.points) or "") if enough else "")
        self.area.queue_draw()

    def frame(self):
        width, height = self.area.get_allocated_width(), self.area.get_allocated_height()
        return (Chart.MARGIN_LEFT, width - Chart.MARGIN_RIGHT, Chart.MARGIN_TOP,
                height - Chart.MARGIN_BOTTOM)

    def on_draw(self, widget, cr):
        left, right, top, bottom = self.frame()
        if right <= left or bottom <= top:
            return False
        color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        foreground = (color.red, color.green, color.blue)
        layout = self.area.create_pango_layout("")
        self.draw_grid(cr, layout, foreground, (left, right, top, bottom))
        # 時鐘落後時可能有時間在未來的紀錄：只畫在繪圖區內
        cr.save()
        cr.rectangle(left - DOT_RADIUS, top - DOT_RADIUS, right - left + 2 * DOT_RADIUS,
                     bottom - top + 2 * DOT_RADIUS)
        cr.clip()
        self.draw_watts(cr, (left, right, top, bottom))
        if self.has_ups:
            self.draw_ups(cr, (left, right, top, bottom))
        cr.restore()
        return False

    def draw_text(self, cr, layout, text, x, y, align):
        """align：0 靠左、0.5 置中、1 靠右；y 為文字垂直中心。"""
        layout.set_text(text, -1)
        width, height = layout.get_pixel_size()
        cr.move_to(x - width * align, y - height / 2)
        PangoCairo.show_layout(cr, layout)

    def draw_grid(self, cr, layout, foreground, frame):
        left, right, top, bottom = frame
        cr.set_line_width(1)
        for fraction in (0.0, 0.5, 1.0):
            y = round(linear(fraction, 0, 1, bottom, top)) + 0.5
            cr.set_source_rgba(*foreground, GRID_ALPHA)
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
            cr.set_source_rgba(*foreground, LABEL_ALPHA)
            self.draw_text(cr, layout, f"{self.axis_max * fraction:.0f} W", left - LABEL_GAP, y, 1)
            if self.has_ups:
                self.draw_text(cr, layout, f"{fraction * 100:.0f}%", right + LABEL_GAP, y, 0)
        for stamp, text in self.ticks:
            x = linear(stamp, self.start, self.end, left, right)
            self.draw_text(cr, layout, text, x, bottom + Chart.MARGIN_BOTTOM / 2, 0.5)

    def draw_watts(self, cr, frame):
        cr.set_line_width(Chart.WATTS_LINE_WIDTH)
        for run in self.watts_runs:
            self.draw_run(cr, run, self.axis_max, frame, WATTS_COLOR)

    def draw_ups(self, cr, frame):
        cr.set_line_width(Chart.UPS_LINE_WIDTH)
        for run in self.ups_runs:
            self.draw_run(cr, run, 100, frame, UPS_COLOR if run[0][2] else UPS_BATTERY_COLOR)

    def draw_run(self, cr, run, top_value, frame, color):
        """一段連續資料：兩點以上畫線，只有一點畫圓點。"""
        left, right, top, bottom = frame
        cr.set_source_rgba(*color, 1)
        for index, (stamp, value, _) in enumerate(run):
            x = linear(stamp, self.start, self.end, left, right)
            y = linear(value, 0, top_value, bottom, top)
            if len(run) == 1:
                cr.arc(x, y, DOT_RADIUS, 0, 2 * math.pi)
                cr.fill()
            elif index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        if len(run) > 1:
            cr.stroke()
