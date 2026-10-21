"""電力流向圖：來源（市電或不斷電系統電池）→ 整機 → 各元件與其他。

靜態圖（本階段沒有動畫），流帶寬度與 √W 成正比，最細 2 px。
文字用主題前景色，流帶用既有配色，明暗主題都看得清楚；內容不變時不重畫。
"""
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo

from sdm.constants import Flow
from sdm.core.chart_layout import flow_geometry, flow_height
from sdm.ui.power_text import watts_text
from sdm.ui.theme import COLORS, WARNING, rgb

CORNER = 10
TEXT_INSET = 8
TWO_LINES_MIN_HEIGHT = 34        # 節點高度不足時改成一行
BAND_ALPHA = 0.5
NODE_FILL_ALPHA = 0.06
NODE_STROKE_ALPHA = 0.22
OTHER_ALPHA = 0.35
ACCENT_WIDTH = 4
SOURCE_COLORS = {"市電": rgb(COLORS["ram"]), "不斷電系統電池": rgb(WARNING)}
ITEM_COLORS = {"gpu": rgb(COLORS["gpu"]), "cpu": rgb(COLORS["cpu"])}


def item_color(item_id, foreground):
    """GPU 紫色、CPU 藍色；「其他」用半透明前景色。"""
    color = ITEM_COLORS.get(item_id.split(":", 1)[0])
    return (color, BAND_ALPHA) if color else (foreground, OTHER_ALPHA)


def rounded_rectangle(cr, rect, radius):
    x, y, width, height = rect
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_band(cr, band, color, alpha):
    """兩條三次貝茲曲線圍成的流帶。"""
    if band.width <= 0:
        return
    middle = (band.x0 + band.x1) / 2
    half = band.width / 2
    cr.move_to(band.x0, band.y0 - half)
    cr.curve_to(middle, band.y0 - half, middle, band.y1 - half, band.x1, band.y1 - half)
    cr.line_to(band.x1, band.y1 + half)
    cr.curve_to(middle, band.y1 + half, middle, band.y0 + half, band.x0, band.y0 + half)
    cr.close_path()
    cr.set_source_rgba(*color, alpha)
    cr.fill()


class PowerFlow(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.model = None
        self.set_size_request(Flow.MIN_WIDTH, Flow.MIN_HEIGHT)
        self.set_hexpand(True)
        self.set_no_show_all(True)   # 沒有資料時保持隱藏，不受 show_all() 影響
        self.connect("draw", self.on_draw)

    def set_model(self, model):
        """model 為 power_text.FlowModel 或 None（隱藏）。"""
        if model == self.model:
            return
        self.model = model
        if model is None:
            self.hide()
            return
        self.set_size_request(Flow.MIN_WIDTH, flow_height(len(model.items)))
        self.show()
        self.queue_draw()

    def on_draw(self, widget, cr):
        model = self.model
        if model is None:
            return False
        geometry = flow_geometry(self.get_allocated_width(), self.get_allocated_height(),
                                 tuple(watts for _, _, watts in model.items), model.total_w)
        color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        foreground = (color.red, color.green, color.blue)
        draw_band(cr, geometry.source_band,
                  SOURCE_COLORS.get(model.source_label, SOURCE_COLORS["市電"]), BAND_ALPHA)
        for band, (item_id, _, _) in zip(geometry.item_bands, model.items):
            draw_band(cr, band, *item_color(item_id, foreground))
        layout = self.create_pango_layout("")
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        self.draw_node(cr, layout, geometry.source, model.source_label, None, foreground)
        self.draw_node(cr, layout, geometry.total, model.total_label, watts_text(model.total_w),
                       foreground)
        for rect, (item_id, label, watts) in zip(geometry.items, model.items):
            accent, _ = item_color(item_id, foreground)
            self.draw_node(cr, layout, rect, label, watts_text(watts), foreground, accent)
        return False

    def draw_node(self, cr, layout, rect, title, value, foreground, accent=None):
        rounded_rectangle(cr, rect, CORNER)
        cr.set_source_rgba(*foreground, NODE_FILL_ALPHA)
        cr.fill_preserve()
        cr.set_source_rgba(*foreground, NODE_STROKE_ALPHA)
        cr.set_line_width(1)
        cr.stroke()
        if accent is not None:
            cr.rectangle(rect.x, rect.y + CORNER / 2, ACCENT_WIDTH, rect.height - CORNER)
            cr.set_source_rgba(*accent, 1)
            cr.fill()
        lines = [GLib.markup_escape_text(title)]
        if value is not None:
            lines.append("<b>" + GLib.markup_escape_text(value) + "</b>")
        separator = "\n" if rect.height >= TWO_LINES_MIN_HEIGHT else "  "
        layout.set_width(int(max(1, rect.width - 2 * TEXT_INSET) * Pango.SCALE))
        layout.set_height(-len(lines))
        layout.set_markup(separator.join(lines), -1)
        _, text_height = layout.get_pixel_size()
        cr.set_source_rgba(*foreground, 1)
        cr.move_to(rect.x + TEXT_INSET + (ACCENT_WIDTH if accent else 0),
                   rect.y + (rect.height - text_height) / 2)
        PangoCairo.show_layout(cr, layout)
