"""配色輔助：十六進位色碼轉 0–1 的 RGB，以及電源頁的 CSS 類別。"""
import unittest

from sdm.ui.theme import COLORS, CRITICAL, WARNING, css_data, rgb


class Colors(unittest.TestCase):
    def test_hex_to_rgb(self):
        self.assertEqual(rgb("#000000"), (0.0, 0.0, 0.0))
        self.assertEqual(rgb("#ffffff"), (1.0, 1.0, 1.0))
        red, green, blue = rgb(COLORS["cpu"])
        self.assertAlmostEqual(red, 0x64 / 255)
        self.assertAlmostEqual(green, 0xa8 / 255)
        self.assertEqual(blue, 1.0)

    def test_invalid_colour_is_rejected(self):
        for value in ("64a8ff", "#64a8f", "#zzzzzz", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    rgb(value)

    def test_power_page_classes_are_in_the_css(self):
        css = css_data().decode()
        for name in (".power-value", ".power-warning", ".power-critical", ".power-state",
                     ".power-headline", ".flow-value", ".metric-value", ".metric"):
            self.assertIn(name, css)
        self.assertIn(WARNING, css)
        self.assertIn(CRITICAL, css)


if __name__ == "__main__":
    unittest.main()
