"""設定 → 外觀：程式圖示的顏色（有預覽）。

換色立即套用到視窗與頂端列，並寫入使用者的圖示主題（見 sdm.services.app_icon）；
寫不進去時在這裡說明原因。GNOME 可能要重新登入後 Dock 才會換圖示。
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from sdm.services import app_icon
from sdm.ui.monitor_widgets import card
from sdm.ui.settings import companion_text as text
from sdm.ui.settings.previews import preview_radios
from sdm.ui.widgets import box

KEY = "app_icon_color"
PREVIEW_SIZE = 56
SOURCE_SIZE = 128


def preview(color):
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(app_icon.asset_path(color, SOURCE_SIZE)), PREVIEW_SIZE, PREVIEW_SIZE, True)
    except GLib.Error:
        return Gtk.Image.new_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
    return Gtk.Image.new_from_pixbuf(pixbuf)


def build(controls, context):
    page = box(spacing=16)
    options = tuple((color, app_icon.TITLES[color], lambda color=color: preview(color))
                    for color in app_icon.COLORS)
    page.pack_start(card("程式圖示", preview_radios(controls, KEY, options),
                         controls.note(lambda: text.icon_note(context.companion.icon_problem()),
                                       {KEY})), False, False, 0)
    return page
