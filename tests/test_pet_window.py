"""桌面寵物視窗的繪製細節（離屏，不需要顯示器）：清除背景後恢復一般疊加，X11 判斷。"""
import unittest

import cairo

from sdm.ui.pet import DesktopPet, is_x11


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
