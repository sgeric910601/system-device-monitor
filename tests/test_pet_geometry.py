"""桌面寵物的大小與位置：縮放範圍、預設大小選項，以及留在螢幕上（單螢幕與雙螢幕）。"""
import math
import unittest

from sdm.core.pet_geometry import (Rect, clamp_scale, default_position, keep_on_screen,
                                   pet_size, preset_title, size_presets)

SINGLE = (Rect(0, 0, 1920, 1080),)
DUAL = (Rect(0, 0, 1920, 1080), Rect(1920, 0, 2560, 1440))


class Scale(unittest.TestCase):
    def test_scale_is_clamped_between_half_and_double(self):
        self.assertEqual([clamp_scale(value) for value in (0.1, 0.5, 1.25, 2.0, 9.0)],
                         [0.5, 0.5, 1.25, 2.0, 2.0])

    def test_invalid_scale_falls_back_to_normal(self):
        for value in (math.nan, math.inf, None, "2"):
            with self.subTest(value=value):
                self.assertEqual(clamp_scale(value), 1.0)

    def test_size_follows_the_base_of_176_px(self):
        self.assertEqual([pet_size(value) for value in (0.75, 1.0, 1.5, 2.0, 5.0)],
                         [132, 176, 264, 352, 352])

    def test_presets_and_their_titles(self):
        self.assertEqual(size_presets(), ((0.75, "小（75%）"), (1.0, "標準（100%）"),
                                          (1.5, "大（150%）"), (2.0, "特大（200%）")))
        self.assertEqual(preset_title(1.25), "125%")
        self.assertEqual(preset_title(1.0), "標準（100%）")


class OnScreen(unittest.TestCase):
    def test_inside_stays_put(self):
        self.assertEqual(keep_on_screen(Rect(100, 200, 176, 176), SINGLE, (0, 0)), (100, 200))

    def test_keeps_48_px_visible_on_each_edge(self):
        self.assertEqual(keep_on_screen(Rect(1900, 1060, 176, 176), SINGLE, (0, 0)), (1872, 1032))
        self.assertEqual(keep_on_screen(Rect(-170, -170, 176, 176), SINGLE, (0, 0)), (-128, -128))

    def test_small_pets_keep_half_their_width(self):
        self.assertEqual(keep_on_screen(Rect(1910, 0, 60, 60), SINGLE, (0, 0)), (1890, 0))

    def test_completely_off_screen_returns_to_the_default(self):
        self.assertEqual(keep_on_screen(Rect(5000, 5000, 176, 176), SINGLE, (1720, 880)),
                         (1720, 880))

    def test_second_monitor_is_a_valid_place(self):
        self.assertEqual(keep_on_screen(Rect(3000, 1200, 176, 176), DUAL, (0, 0)), (3000, 1200))

    def test_below_the_shorter_monitor_clamps_to_the_taller_one(self):
        self.assertEqual(keep_on_screen(Rect(1760, 1300, 176, 176), DUAL, (0, 0)), (1920 - 128, 1300))

    def test_gap_beside_a_shorter_monitor_uses_the_monitor_with_most_overlap(self):
        x, y = keep_on_screen(Rect(1850, 1050, 176, 176), DUAL, (0, 0))
        self.assertEqual((x, y), (1850, 1050))

    def test_no_monitors_uses_the_default(self):
        self.assertEqual(keep_on_screen(Rect(10, 10, 176, 176), (), (5, 6)), (5, 6))

    def test_default_is_the_bottom_right_of_the_work_area(self):
        self.assertEqual(default_position(Rect(0, 32, 1920, 1048), 176), (1720, 880))


if __name__ == "__main__":
    unittest.main()
