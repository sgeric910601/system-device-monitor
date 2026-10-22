"""電力流向圖：來源（市電或不斷電系統電池）→ 整機 → 各元件與其他。

流帶寬度與 √W 成正比，最細 2 px。顯示中、視窗看得到、沒有最小化且省電模式沒有生效時，
流帶上的虛線依瓦數往右流動（最多每秒 15 格，見 flow_animation）；其他時候是靜態圖，
在流帶末端畫箭頭，不留任何計時器。
文字用主題前景色，流帶用既有配色，明暗主題都看得清楚；內容不變時不重畫。
"""
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo

from sdm.constants import Flow
from sdm.core.chart_layout import flow_geometry, flow_height
from sdm.ui.flow_animation import FlowAnimator, dash_speed, should_animate
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
DASH_ALPHA = 0.55
DASH_WIDTH_RATIO = 0.35
DASH_WIDTH_MAX = 4.0
ARROW_ALPHA = 0.9
SOURCE_KEY = "source"
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


def band_center(cr, band):
    middle = (band.x0 + band.x1) / 2
    cr.move_to(band.x0, band.y0)
    cr.curve_to(middle, band.y0, middle, band.y1, band.x1, band.y1)


def draw_dashes(cr, band, color, phase):
    """沿著流帶中線的虛線；相位越大越往右（流向）移動。"""
    if band.width <= 0:
        return
    cr.save()
    band_center(cr, band)
    cr.set_line_width(max(1.0, min(DASH_WIDTH_MAX, band.width * DASH_WIDTH_RATIO)))
    cr.set_dash([Flow.DASH_LENGTH, Flow.DASH_GAP], Flow.DASH_PERIOD - phase)
    cr.set_source_rgba(*color, DASH_ALPHA)
    cr.stroke()
    cr.restore()


def draw_arrow(cr, band, color):
    """靜態圖：流帶末端的箭頭。"""
    if band.width <= 0:
        return
    size = Flow.ARROW_SIZE
    tip = band.x1 - 1
    cr.move_to(tip, band.y1)
    cr.line_to(tip - size, band.y1 - size / 2)
    cr.line_to(tip - size, band.y1 + size / 2)
    cr.close_path()
    cr.set_source_rgba(*color, ARROW_ALPHA)
    cr.fill()


def remove_source(source):
    if GLib.MainContext.default().find_source_by_id(source):
        GLib.source_remove(source)


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
        self.window_visible, self.iconified, self.saving = True, False, False
        self.animator = FlowAnimator(self.queue_draw, GLib.timeout_add, remove_source)
        self.set_size_request(Flow.MIN_WIDTH, Flow.MIN_HEIGHT)
        self.set_hexpand(True)
        self.set_no_show_all(True)   # 沒有資料時保持隱藏，不受 show_all() 影響
        self.connect("draw", self.on_draw)
        self.connect("map", self.update_motion)
        self.connect("unmap", self.update_motion)

    def set_motion(self, window_visible, iconified, saving):
        """視窗是否看得到、是否最小化、省電模式是否生效（由看板設定）。"""
        self.window_visible, self.iconified, self.saving = window_visible, iconified, saving
        self.update_motion()

    def update_motion(self, *_):
        running = self.model is not None and should_animate(
            self.get_mapped(), self.window_visible, self.iconified, self.saving)
        self.animator.set_running(running)

    def set_model(self, model):
        """model 為 power_text.FlowModel 或 None（隱藏）。"""
        if model == self.model:
            return
        self.model = model
        if model is None:
            self.animator.set_speeds({})
            self.hide()
            self.update_motion()
            return
        speeds = {item_id: dash_speed(watts) for item_id, _, watts in model.items}
        self.animator.set_speeds({SOURCE_KEY: dash_speed(model.total_w), **speeds})
        self.set_size_request(Flow.MIN_WIDTH, flow_height(len(model.items)))
        self.show()
        self.update_motion()
        self.queue_draw()

    def on_draw(self, widget, cr):
        model = self.model
        if model is None:
            return False
        geometry = flow_geometry(self.get_allocated_width(), self.get_allocated_height(),
                                 tuple(watts for _, _, watts in model.items), model.total_w)
        color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        foreground = (color.red, color.green, color.blue)
        source_color = SOURCE_COLORS.get(model.source_label, SOURCE_COLORS["市電"])
        draw_band(cr, geometry.source_band, source_color, BAND_ALPHA)
        for band, (item_id, _, _) in zip(geometry.item_bands, model.items):
            draw_band(cr, band, *item_color(item_id, foreground))
        self.draw_motion(cr, geometry, source_color, foreground)
        layout = self.create_pango_layout("")
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        self.draw_node(cr, layout, geometry.source, model.source_label, None, foreground)
        self.draw_node(cr, layout, geometry.total, model.total_label, watts_text(model.total_w),
                       foreground)
        for rect, (item_id, label, watts) in zip(geometry.items, model.items):
            accent, _ = item_color(item_id, foreground)
            self.draw_node(cr, layout, rect, label, watts_text(watts), foreground, accent)
        return False

    def draw_motion(self, cr, geometry, source_color, foreground):
        """動畫時畫流動的虛線，靜態時畫箭頭。"""
        bands = ((SOURCE_KEY, geometry.source_band, source_color),) + tuple(
            (item_id, band, item_color(item_id, foreground)[0])
            for band, (item_id, _, _) in zip(geometry.item_bands, self.model.items))
        phases = self.animator.phases
        for key, band, color in bands:
            if self.animator.running:
                draw_dashes(cr, band, foreground, phases.get(key, 0.0))
            else:
                draw_arrow(cr, band, color)

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
