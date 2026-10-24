"""桌面寵物：把充電夥伴放在桌面上（X11）。

- 沒有邊框的透明視窗，不出現在工作列與視窗切換，每個工作區都看得到；
  「浮在視窗上」時保持在最上層，「放在桌布上」時保持在最下層。
- 只有畫出寵物的地方接收滑鼠（輸入範圍依圖案的透明度），點在透明處等於點在下面的東西。
- 拖曳移動（交給視窗管理員），放開後確保至少一角留在螢幕上並記住位置（XDG 狀態目錄）。
- 滑鼠移過去：想像框顯示電量與狀態（桌機為整機耗電與供電來源）；點一下：對話框說下一句話，
  4 秒後收起；左右來回摸牠：冒愛心、說呼嚕呼嚕；連點兩下：打開看板的「電源」頁；
  右鍵：選單（位置、大小、外觀、設定、關閉）。
- 改大小時腳底的位置不動。動畫與眼睛的規則與電源頁的夥伴相同（只在省電模式時停止）。
- Wayland 不能自己決定視窗位置與層級：改成一般的小視窗（有標題列），不記位置、沒有穿透。
"""
import random
import time
from typing import Callable, NamedTuple

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from sdm.constants import Companion, Pet
from sdm.core.pet_geometry import (Rect, bubble_origin, default_position, is_drag,
                                   keep_on_screen, pet_size, resized_origin)
from sdm.core.pet_motion import StrokeDetector, hearts, hearts_done, next_saying
from sdm.core.pet_messages import PURRS
from sdm.core.sprite_motion import frame_interval_ms
from sdm.ui import theme
from sdm.ui.companion import CompanionView, remove_source
from sdm.ui.companion_draw import draw_figure
from sdm.ui.pet_bubble import PetBubble
from sdm.services.pet_state import PositionKeeper
from sdm.ui.pet_menu import pet_menu

TITLE = "桌面寵物"
LEFT_BUTTON, RIGHT_BUTTON = 1, 3
MILLISECONDS = 1000
HEART = theme.rgb(theme.COMPANION["heart"])
EVENTS = (Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
          | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
          | Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.STRUCTURE_MASK)


class PetHost(NamedTuple):
    """寵物需要看板提供的東西（讓寵物不必知道看板的其他部分）。"""
    photos: object                          # PhotoStore
    images: object                          # sprite.Images
    position: object                        # PetPosition
    preferences: Callable[[], object]       # 目前的設定
    thought: Callable[[], object]           # 想像框文字；沒有資料時為 None
    sayings: Callable[[], list]             # 點一下時依序說的話
    update: Callable[..., None]             # 寫入設定
    open_power: Callable[[], None]
    open_settings: Callable[[], None]


def is_x11(display):
    return "X11" in type(display).__name__


def heart_path(cr, x, y, size):
    """愛心：上方兩個圓弧，下方收成尖角；x、y 為中心。"""
    half = size / 2
    cr.move_to(x, y + half)
    cr.curve_to(x - size * 0.9, y - size * 0.05, x - half, y - size * 0.8, x, y - size * 0.25)
    cr.curve_to(x + half, y - size * 0.8, x + size * 0.9, y - size * 0.05, x, y + half)
    cr.close_path()


class PetView(CompanionView):
    """桌面寵物的圖案：再加上被摸時冒出的愛心；外觀改變時通知重算輸入範圍。"""

    def __init__(self, photos, images, shape_changed):
        super().__init__(photos, images, pet=True)
        self.shape_changed = shape_changed
        self.hearts_start, self.hearts_source = None, None

    def configure(self, appearance=None, state=None, live_eyes=None, follow_cursor=None):
        before = (self.appearance, self.state.on_pad)
        super().configure(appearance, state, live_eyes, follow_cursor)
        if (self.appearance, self.state.on_pad) != before:
            self.shape_changed()

    def burst(self):
        self.hearts_start = time.monotonic()
        if self.hearts_source is None:
            self.hearts_source = GLib.timeout_add(frame_interval_ms(Companion.MAX_FPS),
                                                  self.heart_frame)
        self.queue_draw()

    def heart_frame(self):
        self.queue_draw()
        if hearts_done(time.monotonic() - self.hearts_start):
            self.hearts_source, self.hearts_start = None, None
            return False
        return True

    def on_draw(self, widget, cr):
        super().on_draw(widget, cr)
        if self.hearts_start is not None:
            size = self.get_allocated_width()
            for heart in hearts(time.monotonic() - self.hearts_start):
                heart_path(cr, heart.x * size, heart.y * size, heart.size * size)
                cr.set_source_rgba(*HEART, heart.alpha)
                cr.fill()
        return False

    def stop(self):
        super().stop()
        if self.hearts_source is not None:
            remove_source(self.hearts_source)
            self.hearts_source, self.hearts_start = None, None


class DesktopPet:
    def __init__(self, host):
        self.host = host
        self.view = PetView(host.photos, host.images, self.update_shape)
        self.bubble = PetBubble()
        self.stroke, self.rng = StrokeDetector(), random.Random()
        self.hovering, self.speech, self.saying_index = False, None, 0
        self.press, self.click_source, self.speech_source, self.save_source = (None,) * 4
        self.size = pet_size(host.preferences().desktop_pet_scale)
        self.window = self.build_window()
        self.keeper = PositionKeeper(host.position if self.x11 else None)

    def build_window(self):
        window = Gtk.Window(title=TITLE)
        window.set_resizable(False)
        self.x11 = is_x11(window.get_display())
        if self.x11:
            window.set_decorated(False)
            window.set_app_paintable(True)
            visual = window.get_screen().get_rgba_visual()
            if visual is not None:
                window.set_visual(visual)
            for hint in (window.set_skip_taskbar_hint, window.set_skip_pager_hint):
                hint(True)
            window.set_accept_focus(False)
            window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            window.connect("draw", self.clear)
        window.add_events(EVENTS)
        for signal, handler in (("button-press-event", self.on_press),
                                ("button-release-event", self.on_release),
                                ("motion-notify-event", self.on_motion),
                                ("enter-notify-event", self.on_enter),
                                ("leave-notify-event", self.on_leave),
                                ("configure-event", self.on_configure),
                                ("realize", lambda *_: self.update_shape()),
                                ("delete-event", self.on_delete)):
            window.connect(signal, handler)
        self.view.set_size(self.size)
        window.add(self.view)
        return window

    @staticmethod
    def clear(_window, cr):
        """先把整個視窗清成透明；之後畫圖案時要恢復一般的疊加方式（同一個 Cairo 狀態會傳給子元件）。"""
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.restore()
        return False

    # 顯示、位置與大小

    def monitors(self):
        display = self.window.get_display()
        return [Rect(*rectangle(display.get_monitor(index).get_geometry()))
                for index in range(display.get_n_monitors())]

    def workarea(self, point=None):
        display = self.window.get_display()
        monitor = (display.get_monitor_at_point(*point) if point is not None
                   else display.get_primary_monitor() or display.get_monitor(0))
        return Rect(*rectangle(monitor.get_workarea()))

    def rect(self):
        x, y = self.window.get_position()
        return Rect(x, y, self.size, self.size)

    def show(self):
        default = default_position(self.workarea(), self.size)
        saved = self.host.position.load() if self.x11 else None
        x, y = keep_on_screen(Rect(*(saved or default), self.size, self.size), self.monitors(),
                              default)
        self.window.move(round(x), round(y))
        self.window.show_all()
        self.window.stick()
        self.apply_layer(self.host.preferences().desktop_pet_on_wallpaper)

    def apply_layer(self, wallpaper):
        self.window.set_keep_above(not wallpaper)
        self.window.set_keep_below(wallpaper)

    def apply(self, wallpaper, scale):
        """位置（浮在視窗上或放在桌布上）與大小；改大小時腳底不動。"""
        self.apply_layer(wallpaper)
        size = pet_size(scale)
        if size == self.size:
            return
        x, y = resized_origin(self.rect(), size)
        self.size = size
        self.keeper.user_moved()
        self.view.set_size(size)
        self.window.resize(size, size)
        default = default_position(self.workarea(), size)
        x, y = keep_on_screen(Rect(x, y, size, size), self.monitors(), default)
        self.window.move(round(x), round(y))
        self.update_shape()
        self.update_bubble()

    def update_shape(self):
        """只有畫出寵物的地方接收滑鼠：以靜態圖的透明度當輸入範圍。"""
        gdk_window = self.window.get_window()
        if not self.x11 or gdk_window is None:
            return
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.size, self.size)
        scene = self.view.scene()._replace(t=0.0, eyes=None)
        draw_figure(cairo.Context(surface), Rect(0, 0, self.size, self.size), scene,
                    self.view.images)
        gdk_window.input_shape_combine_region(Gdk.cairo_region_create_from_surface(surface), 0, 0)

    def set_motion(self, saving):
        self.view.set_motion(True, saving)

    def on_configure(self, *_):
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(Pet.SAVE_DELAY_MS, self.settle)
        self.update_bubble()
        return False

    def settle(self):
        """移動停下來之後：確保至少一角在螢幕上；使用者拖曳或改大小過才記住位置。"""
        self.save_source = None
        rect = self.rect()
        x, y = self.keeper.settle(rect, self.monitors(), default_position(self.workarea(), self.size))
        if (x, y) != (rect.x, rect.y):
            self.window.move(x, y)
        return False

    def destroy(self):
        """關閉寵物：還沒存的位置先存起來，再移除所有計時器與視窗。"""
        if self.save_source is not None:
            remove_source(self.save_source)
            if self.window.get_visible():
                self.settle()
        for source in (self.click_source, self.speech_source):
            if source is not None:
                remove_source(source)
        self.view.stop()
        self.bubble.destroy()
        self.window.destroy()

    def on_delete(self, *_):
        """Wayland 的一般視窗被關掉：等於關閉桌面寵物。"""
        self.host.update(desktop_pet=False)
        return True

    # 想像框與對話框

    def status_changed(self):
        if self.hovering and self.speech is None:
            self.update_bubble()

    def update_bubble(self):
        text = self.speech or (self.host.thought() if self.hovering else None)
        if not text or not self.window.get_visible():
            self.bubble.hide()
            return
        rect = self.rect()
        center = (round(rect.x + rect.width / 2), round(rect.y + rect.height / 2))
        origin = bubble_origin(rect, (Pet.BUBBLE_WIDTH, Pet.BUBBLE_HEIGHT),
                               self.workarea(center), self.size / Pet.BASE_SIZE)
        self.bubble.show(text, self.speech is None, origin)

    def say(self, text):
        self.speech = text
        if self.speech_source is not None:
            remove_source(self.speech_source)
        self.speech_source = GLib.timeout_add_seconds(Pet.SPEECH_SECONDS, self.quiet)
        self.update_bubble()

    def quiet(self):
        self.speech, self.speech_source = None, None
        self.update_bubble()
        return False

    def speak(self):
        self.click_source = None
        text = next_saying(self.host.sayings(), self.saying_index)
        self.saying_index += 1
        if text:
            self.say(text)
        return False

    def petted(self):
        self.view.burst()
        self.say(self.rng.choice(PURRS))

    # 滑鼠

    def cancel_click(self):
        if self.click_source is not None:
            remove_source(self.click_source)
            self.click_source = None

    def on_press(self, _window, event):
        if event.button == RIGHT_BUTTON and event.type == Gdk.EventType.BUTTON_PRESS:
            self.show_menu(event)
            return True
        if event.button != LEFT_BUTTON:
            return False
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self.cancel_click()
            self.press = None
            self.host.open_power()
        elif event.type == Gdk.EventType.BUTTON_PRESS:
            self.press = (event.x_root, event.y_root)
        return True

    def on_motion(self, window, event):
        if self.press is not None and event.state & Gdk.ModifierType.BUTTON1_MASK:
            if is_drag(self.press, (event.x_root, event.y_root)):
                self.press = None
                self.cancel_click()
                self.keeper.user_moved()
                window.begin_move_drag(LEFT_BUTTON, round(event.x_root), round(event.y_root),
                                       event.time)
            return True
        if self.hovering and self.stroke.feed(event.x, time.monotonic()):
            self.petted()
        return False

    def on_release(self, _window, event):
        if event.button != LEFT_BUTTON or self.press is None:
            return False
        self.press = None
        self.cancel_click()
        delay = Gtk.Settings.get_default().get_property("gtk-double-click-time")
        self.click_source = GLib.timeout_add(delay, self.speak)
        return True

    def on_enter(self, *_):
        self.hovering = True
        self.stroke.reset()
        self.update_bubble()
        return False

    def on_leave(self, _window, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.hovering = False
        self.stroke.reset()
        self.update_bubble()
        return False

    def show_menu(self, event):
        menu = pet_menu(self.host.preferences(), self.host.photos.has_photo(), self.host.update,
                        self.host.open_power, self.host.open_settings)
        menu.attach_to_widget(self.window, None)
        menu.popup_at_pointer(event)


def rectangle(gdk_rectangle):
    return gdk_rectangle.x, gdk_rectangle.y, gdk_rectangle.width, gdk_rectangle.height
