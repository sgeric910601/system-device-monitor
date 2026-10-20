"""共用 GTK 元件：標籤、容器與最近五分鐘曲線圖。"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def label(text="", css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(wrap)
    if css:
        widget.get_style_context().add_class(css)
    return widget


def box(vertical=True, spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                   spacing=spacing)


def clear(widget):
    for child in widget.get_children():
        widget.remove(child)


class Graph(Gtk.DrawingArea):
    def __init__(self, series, color):
        super().__init__()
        self.series, self.color = series, color
        self.set_size_request(200, 78)
        self.set_hexpand(True)
        self.connect("draw", self.draw)

    def draw(self, widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        cr.set_line_width(1)
        cr.set_source_rgba(.5, .5, .5, .18)
        for y in (height*.25, height*.5, height*.75):
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()
        if not self.series:
            return
        end = time.monotonic()
        cr.set_source_rgba(*self.color, .95)
        cr.set_line_width(2)
        previous = None
        for stamp, value in self.series:
            if value is None:
                previous = None
                continue
            x = width * (1 - (end-stamp)/300)
            y = height - max(0, min(100, value))/100*(height-8)-4
            if previous is None or stamp - previous > 5:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
            previous = stamp
        cr.stroke()
