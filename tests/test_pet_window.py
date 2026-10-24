"""桌面寵物視窗的繪製細節（離屏，不需要顯示器）：清除背景後恢復一般疊加，X11 判斷。"""
import unittest

import cairo

from sdm.core.companion import BUSY, CHARGING_MOOD, IDLE, CompanionState
from sdm.core.pet_geometry import Rect
from sdm.ui.pet import DesktopPet, is_x11, monitor_rects, shape_key


class Clear(unittest.TestCase):
    def test_clearing_keeps_normal_blending_for_the_figure(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4)
        cr = cairo.Context(surface)
        cr.set_source_rgb(1, 0, 0)
        cr.paint()
        DesktopPet.clear(None, cr)
        surface.flush()
        self.assertEqual(cr.get_operator(), cairo.OPERATOR_OVER)
        self.assertEqual(bytes(surface.get_data()), bytes(4 * 4 * 4))
        cr.rectangle(0, 0, 2, 4)
        cr.set_source_rgba(0, 0, 1, 1)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        surface.flush()
        self.assertNotEqual(bytes(surface.get_data())[:4], bytes(4))


class Shape(unittest.TestCase):
    """點擊範圍依圖案的透明度：任何會改變外形的東西都要重算。"""

    def test_mood_level_and_charging_change_the_shape(self):
        base = shape_key("buddy", CompanionState(IDLE, 0.5), 0)
        for state in (CompanionState(BUSY, 0.5), CompanionState(IDLE, 0.6),
                      CompanionState(CHARGING_MOOD, 0.5, on_pad=True)):
            with self.subTest(state=state):
                self.assertNotEqual(shape_key("buddy", state, 0), base)

    def test_photo_version_matters_only_for_my_photo(self):
        state = CompanionState(IDLE, 0.5)
        self.assertNotEqual(shape_key("photo", state, 1), shape_key("photo", state, 2))
        self.assertEqual(shape_key("cat", state, 1), shape_key("cat", state, 2))
        self.assertNotEqual(shape_key("cat", state, 1), shape_key("headshot", state, 1))


class Box:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class FakeMonitor:
    def __init__(self, geometry, workarea):
        self.geometry, self.workarea = geometry, workarea

    def get_geometry(self):
        return self.geometry

    def get_workarea(self):
        return self.workarea


class FakeDisplay:
    def __init__(self, monitors):
        self.monitors = monitors

    def get_n_monitors(self):
        return len(self.monitors)

    def get_monitor(self, index):
        return self.monitors[index]


class Monitors(unittest.TestCase):
    def test_work_areas_are_used_not_the_full_screens(self):
        display = FakeDisplay([FakeMonitor(Box(0, 0, 1920, 1080), Box(0, 32, 1920, 1048)),
                               FakeMonitor(Box(1920, 0, 2560, 1440), Box(1920, 0, 2560, 1400))])
        self.assertEqual(monitor_rects(display), [Rect(0, 32, 1920, 1048),
                                                  Rect(1920, 0, 2560, 1400)])


class Session(unittest.TestCase):
    def test_x11_is_detected_from_the_display_type(self):
        class GdkX11Display:
            pass

        class GdkWaylandDisplay:
            pass
        self.assertTrue(is_x11(GdkX11Display()))
        self.assertFalse(is_x11(GdkWaylandDisplay()))


if __name__ == "__main__":
    unittest.main()
