"""設定 → 夥伴：充電夥伴的外觀（有預覽）、會動的眼睛、我的照片與動畫說明。

外觀與眼睛的設定同時用在「電源」頁的夥伴與桌面寵物。我的照片在示範模式停用。
"""
from dataclasses import dataclass
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.companion import APPEARANCES, GOOD, IDLE, PHOTO, TITLES, CompanionState
from sdm.core.pet_geometry import Rect
from sdm.ui.companion_draw import Scene, draw_figure
from sdm.ui.monitor_widgets import card
from sdm.ui.settings import companion_text as text
from sdm.ui.settings.controls import wrapped
from sdm.ui.settings.general import SAVING_KEYS
from sdm.ui.settings.previews import preview_radios
from sdm.ui.sprite import surface_from_pixbuf
from sdm.ui.widgets import box

KEY = "companion_appearance"
PHOTO_CHANGED = "_photo"        # 換了照片（不是設定）：更新預覽與按鈕
PREVIEW_WIDTH, PREVIEW_HEIGHT = 56, 58
PREVIEW_STATE = CompanionState(IDLE, 0.7, GOOD)


@dataclass(frozen=True)
class CompanionContext:
    """設定視窗需要看板提供的夥伴資料。"""
    photos: object                              # PhotoStore
    images: object                              # sprite.Images
    photo_changed: Callable[[], None]           # 換照片或刪除後重畫夥伴與桌面寵物
    icon_problem: Callable[[], Optional[str]]   # 程式圖示寫不進去的原因
    saving: Callable[[], bool]                  # 省電模式是否生效
    x11: bool                                   # 桌面寵物能不能放在桌布上（Wayland 不行）


def preview(companion, appearance):
    area, cache = Gtk.DrawingArea(), {}
    area.set_size_request(PREVIEW_WIDTH, PREVIEW_HEIGHT)

    def own_photo():
        """我的照片只在第一次與換照片後讀取。"""
        version = companion.photos.version
        if cache.get("version") != version:
            pixbuf = companion.photos.load()
            cache.update(version=version,
                         surface=None if pixbuf is None else surface_from_pixbuf(pixbuf))
        return cache["surface"]

    def draw(widget, cr):
        color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        photo = own_photo() if appearance == PHOTO else None
        scene = Scene(appearance, PREVIEW_STATE, 0.0, (color.red, color.green, color.blue),
                      False, photo)
        draw_figure(cr, Rect(0, 0, widget.get_allocated_width(),
                             widget.get_allocated_height()), scene, companion.images)
        return False
    area.connect("draw", draw)
    return area


def looks_card(controls, companion):
    options = tuple((name, TITLES[name], lambda name=name: preview(companion, name))
                    for name in APPEARANCES)
    radios = preview_radios(controls, KEY, options,
                            enabled=lambda name: name != PHOTO or companion.photos.has_photo(),
                            keys={PHOTO_CHANGED})
    controls.bind({PHOTO_CHANGED}, radios.queue_draw)
    return card("外觀", radios, wrapped(text.CYCLE_NOTE, "muted"),
                controls.switch("desktop_pet_live_eyes", text.LIVE_EYES))


def choose_dialog(parent):
    dialog = Gtk.FileChooserNative.new(text.CHOOSER_TITLE, parent, Gtk.FileChooserAction.OPEN,
                                       text.CHOOSER_ACCEPT, None)
    images = Gtk.FileFilter()
    images.set_name("圖片")
    images.add_pixbuf_formats()
    dialog.add_filter(images)
    return dialog


def photo_card(controls, companion):
    status = wrapped("", "power-warning")
    status.set_no_show_all(True)

    def changed():
        companion.photo_changed()
        controls.changed(frozenset({PHOTO_CHANGED}))

    def choose(button):
        dialog = choose_dialog(button.get_toplevel())
        path = dialog.get_filename() if dialog.run() == Gtk.ResponseType.ACCEPT else None
        if path:
            problem = text.choose_photo(companion.photos, path, controls.apply, changed)
            status.set_text(problem or "")
            status.set_visible(bool(problem))
        dialog.destroy()

    def remove(_button):
        text.remove_photo(companion.photos, controls.store.value(KEY), controls.apply, changed)
    buttons = box(False, 8)
    for title, action, when in ((text.CHOOSE, choose, lambda: companion.photos.enabled),
                                (text.REMOVE, remove, companion.photos.has_photo)):
        button = Gtk.Button(label=title)
        button.connect("clicked", action)
        controls.enable_when(button, KEY, when=when)
        buttons.pack_start(button, False, False, 0)
    return card("我的照片", buttons, status, wrapped(text.photo_note(companion.photos), "muted"))


def build(controls, context):
    page, companion = box(spacing=16), context.companion
    page.pack_start(looks_card(controls, companion), False, False, 0)
    page.pack_start(photo_card(controls, companion), False, False, 0)
    page.pack_start(card("動畫", controls.note(lambda: text.animation_note(companion.saving()),
                                               SAVING_KEYS)), False, False, 0)
    return page
