"""桌面寵物的想像框（滑鼠移過去，圓角加兩個小圓點）與對話框（點一下，圓角加三角形尾巴）。

固定大小的透明彈出視窗，完全不接收滑鼠（輸入範圍是空的），不會擋到下面的東西。
框貼著靠近寵物的那一邊畫：在寵物上方時貼底、尾巴朝下；在下方時貼頂、尾巴朝上。
"""
import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo

from sdm.constants import Pet
from sdm.ui import theme
from sdm.ui.companion_draw import rounded

PADDING_X, PADDING_Y = 12, 8
MARGIN = 6                      # 視窗邊緣到框（留給陰影）
TAIL = 10                       # 尾巴（或兩個小圓點）佔的高度
SPEECH_RADIUS, THOUGHT_RADIUS = 10, 16
BORDER_ALPHA, SHADOW_ALPHA = 0.35, 0.16
BORDER = (0.5, 0.5, 0.5)
FILL = theme.rgb(theme.COMPANION["bubble"])
TEXT = theme.rgb(theme.COMPANION["bubble_text"])


class PetBubble:
    def __init__(self):
        self.text, self.thought, self.below = "", False, False
        self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.window.set_app_paintable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual is not None:
            self.window.set_visual(visual)
        self.window.set_size_request(Pet.BUBBLE_WIDTH, Pet.BUBBLE_HEIGHT)
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.on_draw)
        self.window.add(self.area)
        self.window.connect("realize", self.pass_through)

    @staticmethod
    def pass_through(window):
        """輸入範圍是空的：點在對話框上等於點在下面的東西。"""
        window.get_window().input_shape_combine_region(cairo.Region(), 0, 0)

    def show(self, text, thought, origin):
        x, y, below = origin
        if (text, thought, below) != (self.text, self.thought, self.below):
            self.text, self.thought, self.below = text, thought, below
            self.area.queue_draw()
        self.window.move(x, y)
        self.window.show_all()

    def hide(self):
        self.window.hide()

    @property
    def visible(self):
        return self.window.get_visible()

    def destroy(self):
        self.window.destroy()

    def layout(self, widget):
        layout = widget.create_pango_layout(self.text)
        layout.set_width(Pet.BUBBLE_TEXT_WIDTH * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_alignment(Pango.Alignment.CENTER)
        return layout

    def box(self, text_width, text_height, width, height):
        box_width = min(width - 2 * MARGIN, text_width + 2 * PADDING_X)
        box_height = min(height - 2 * MARGIN - TAIL, text_height + 2 * PADDING_Y)
        x = (width - box_width) / 2
        y = MARGIN + TAIL if self.below else height - MARGIN - TAIL - box_height
        return x, y, box_width, box_height

    def on_draw(self, widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if not self.text:
            return True
        layout = self.layout(widget)
        _, logical = layout.get_pixel_extents()
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        x, y, box_width, box_height = self.box(logical.width, logical.height, width, height)
        radius = THOUGHT_RADIUS if self.thought else SPEECH_RADIUS
        rounded(cr, x + 1, y + 2, box_width, box_height, radius)
        cr.set_source_rgba(0, 0, 0, SHADOW_ALPHA)
        cr.fill()
        self.draw_tail(cr, width / 2, y, box_height)
        rounded(cr, x, y, box_width, box_height, radius)
        cr.set_source_rgb(*FILL)
        cr.fill_preserve()
        cr.set_source_rgba(*BORDER, BORDER_ALPHA)
        cr.set_line_width(1)
        cr.stroke()
        cr.set_source_rgb(*TEXT)
        cr.move_to(x + (box_width - logical.width) / 2 - logical.x, y + PADDING_Y - logical.y)
        PangoCairo.show_layout(cr, layout)
        return True

    def draw_tail(self, cr, cx, top, box_height):
        """對話框：三角形尾巴指向寵物；想像框：兩個小圓點，離寵物越近的越小。"""
        edge = top if self.below else top + box_height
        step = -1 if self.below else 1
        if self.thought:
            for offset, radius in ((3.5, 3.5), (8.5, 2.2)):
                cr.arc(cx - 8, edge + step * offset, radius, 0, 6.3)
                cr.set_source_rgb(*FILL)
                cr.fill_preserve()
                cr.set_source_rgba(*BORDER, BORDER_ALPHA)
                cr.set_line_width(1)
                cr.stroke()
            return
        cr.move_to(cx - 7, edge - step)
        cr.line_to(cx, edge + step * 8)
        cr.line_to(cx + 7, edge - step)
        cr.close_path()
        cr.set_source_rgb(*FILL)
        cr.fill_preserve()
        cr.set_source_rgba(*BORDER, BORDER_ALPHA)
        cr.set_line_width(1)
        cr.stroke()
