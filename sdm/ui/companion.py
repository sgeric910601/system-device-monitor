"""充電夥伴元件（Gtk.DrawingArea）：電源頁與桌面寵物共用，圖案由 companion_draw 畫。

- 外觀、表情、會動的眼睛與省電狀態由外部設定；改變時只重畫，不重建元件。
- 計時器只在需要時存在（sprite_motion.motion_plan）：看不到（沒有顯示、視窗隱藏、最小化
  或被完全遮住）或省電模式生效時全部移除，畫第一格的靜態圖。
  - 逐格動畫（貓咪呼吸、粒子、電量條光點）：每秒最多 12 格。
  - 眨眼：電池夥伴醒著時與貓咪大頭照。
  - 游標：貓咪大頭照的眼睛跟著游標時，每秒讀 15 次游標位置，眼珠移動得夠多才重畫。
- 電源頁（interactive）：點一下換下一個外觀，右鍵選單列出所有外觀。
- 我的照片只在 PhotoStore 啟用時讀取（示範模式與模擬測試畫貓咪大頭照）。
"""
import random

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from sdm.constants import Companion
from sdm.core.companion import APPEARANCES, PHOTO, TITLES, CompanionState
from sdm.core.pet_geometry import Rect
from sdm.core.pet_motion import BlinkTimer, eye_circles, gaze, gaze_moved
from sdm.core.sprite_motion import MotionPlan, Ticker, motion_plan
from sdm.ui.companion_draw import EyeScene, Scene, draw_figure, photo_rect
from sdm.ui.pet_menu import NO_PHOTO, action, radio_items
from sdm.ui.sprite import Images, surface_from_pixbuf

MILLISECONDS = 1000
PAGE_HEIGHT_RATIO = 50 / 48
TIP = "點一下換外觀，按右鍵選擇外觀"
SETTINGS = "夥伴設定…"
LEFT_BUTTON, RIGHT_BUTTON = 1, 3
STOPPED = MotionPlan(False, False, False)


def remove_source(source):
    if GLib.MainContext.default().find_source_by_id(source):
        GLib.source_remove(source)


class CompanionView(Gtk.DrawingArea):
    def __init__(self, photos, images=None, pet=False, interactive=False):
        super().__init__()
        self.photos, self.images, self.pet = photos, images or Images(), pet
        self.appearance, self.state = APPEARANCES[0], CompanionState()
        self.live_eyes, self.follow_cursor = True, True
        self.window_visible, self.saving = True, False
        self.photo, self.photo_version, self.gaze = None, None, ((0.0, 0.0),) * 2
        self.plan, self.pointer_source = STOPPED, None
        self.ticker = Ticker(self.queue_draw, GLib.timeout_add, remove_source)
        self.blink = BlinkTimer(self.queue_draw, GLib.timeout_add, remove_source,
                                random.Random())
        self.on_select, self.on_settings = (lambda name: None), (lambda: None)
        self.set_size(Companion.PAGE_WIDTH)
        self.connect("draw", self.on_draw)
        for signal in ("map", "unmap"):
            self.connect(signal, lambda *_: self.refresh_motion())
        if interactive:
            self.make_interactive()

    def set_size(self, width):
        height = width if self.pet else round(width * PAGE_HEIGHT_RATIO)
        self.set_size_request(width, height)

    def make_interactive(self):
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.set_tooltip_text(TIP)
        self.connect("button-press-event", self.on_button)
        self.connect("realize", lambda widget: widget.get_window().set_cursor(
            Gdk.Cursor.new_from_name(widget.get_display(), "pointer")))

    def configure(self, appearance=None, state=None, live_eyes=None, follow_cursor=None):
        """更新任何一項；沒有改變時不重畫。"""
        values = dict(appearance=appearance, state=state, live_eyes=live_eyes,
                      follow_cursor=follow_cursor)
        changed = False
        for name, value in values.items():
            if value is not None and getattr(self, name) != value:
                setattr(self, name, value)
                changed = True
        if changed:
            self.refresh_motion()
            self.queue_draw()

    def set_motion(self, window_visible, saving):
        """所在的視窗看得到（沒有隱藏、最小化或被完全遮住）、省電模式是否生效。"""
        self.window_visible, self.saving = window_visible, saving
        self.refresh_motion()

    def refresh_motion(self):
        visible = self.get_mapped() and self.window_visible
        plan = motion_plan(self.appearance, self.state, self.live_eyes, self.follow_cursor,
                           visible, self.saving)
        self.ticker.set_running(plan.ticker)
        self.blink.set_running(plan.blink)
        self.set_pointer(plan.pointer)
        self.plan = plan

    def set_pointer(self, watching):
        if watching == (self.pointer_source is not None):
            return
        if watching:
            interval = round(MILLISECONDS / Companion.POINTER_HZ)
            self.pointer_source = GLib.timeout_add(interval, self.poll_pointer)
            self.poll_pointer()
            return
        remove_source(self.pointer_source)
        self.pointer_source = None
        self.gaze = ((0.0, 0.0),) * 2
        self.queue_draw()

    def stop(self):
        """元件不再使用：移除所有計時器。"""
        self.ticker.set_running(False)
        self.blink.set_running(False)
        self.set_pointer(False)

    def eye_centers(self):
        """視窗座標中的眼睛中心（與 companion_draw 的版面相同）。"""
        rect = photo_rect(self.figure_rect(), self.pet)
        return [(x, y) for x, y, _ in eye_circles(rect.width, rect.x, rect.y)]

    def poll_pointer(self):
        window = self.get_window()
        if window is None:
            return True
        _, left, top = window.get_origin()
        pointer = self.get_display().get_default_seat().get_pointer()
        _, x, y = pointer.get_position()
        width = self.get_allocated_width()
        current = tuple(gaze((left + cx, top + cy), (x, y), width)
                        for cx, cy in self.eye_centers())
        if gaze_moved(self.gaze, current):
            self.gaze = current
            self.queue_draw()
        return True

    def figure_rect(self):
        return Rect(0, 0, self.get_allocated_width(), self.get_allocated_height())

    def own_photo(self):
        """我的照片的 Cairo 圖面；第一次用到與換照片或刪除後才重新載入。"""
        if self.photo_version != self.photos.version:
            self.photo_version = self.photos.version
            pixbuf = self.photos.load()
            self.photo = None if pixbuf is None else surface_from_pixbuf(pixbuf)
        return self.photo

    def scene(self):
        color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        eyes = None
        if self.plan.blink or self.plan.pointer:
            eyes = EyeScene(self.gaze, self.blink.lid)
        photo = self.own_photo() if self.appearance == PHOTO else None
        return Scene(self.appearance, self.state, self.ticker.elapsed,
                     (color.red, color.green, color.blue), self.pet, photo, eyes)

    def on_draw(self, _widget, cr):
        draw_figure(cr, self.figure_rect(), self.scene(), self.images)
        return False

    def on_button(self, _widget, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if event.button == LEFT_BUTTON:
            self.on_select(None)
        elif event.button == RIGHT_BUTTON:
            appearance_menu(self.appearance, self.photos.has_photo(), self.on_select,
                            self.on_settings).popup_at_pointer(event)
        return True


def appearance_menu(current, has_photo, select, open_settings):
    """右鍵選單：所有外觀（目前的打勾）、夥伴設定。"""
    menu = Gtk.Menu()
    looks = tuple((name, TITLES[name] if has_photo or name != PHOTO else NO_PHOTO)
                  for name in APPEARANCES)
    radio_items(menu, looks, lambda name: name == current, select,
                enabled=lambda name: has_photo or name != PHOTO)
    menu.append(Gtk.SeparatorMenuItem())
    menu.append(action(SETTINGS, open_settings))
    menu.show_all()
    return menu
