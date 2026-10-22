"""頂端列耗電圖示：電池與插頭／不斷電系統圖示的 SVG，以及只在內容改變時輪替寫檔。

顏色規則沿用參考專案 StatusIconRenderer：電量低（≤ 低電量門檻）且正在放電為紅色，
接著電源為綠色；充電中疊上閃電，接電未充電（含已充飽、已達上限）疊上插頭。
"""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from xml.etree import ElementTree

from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              BatterySnapshot)
from sdm.core.estimates import Estimates
from sdm.core.power_breakdown import Breakdown
from sdm.data.ups import UpsReading
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import DesktopPower
from sdm.ui import theme
from sdm.ui.tray_icons import (BatteryGlyph, DesktopGlyph, IconWriter, power_glyph, render)


def laptop(percent=82, state=DISCHARGING, draining=False):
    return LaptopPower(stamp=0.0, battery=BatterySnapshot(percent=percent, ac_online=state != DISCHARGING),
                       estimates=Estimates(state=state, draining_on_ac=draining),
                       breakdown=Breakdown())


def desktop(on_battery=False, low=False, charge=100.0):
    ups = UpsReading(online=not on_battery, on_battery=on_battery, low_battery=low,
                     charge_pct=charge)
    return DesktopPower(stamp=0.0, system_w=200.0, average_w=None, source="ups",
                        supply="mains", breakdown=Breakdown(), ups=ups, gpus=())


def size(svg):
    root = ElementTree.fromstring(svg)
    return float(root.get("width")), float(root.get("height"))


class Glyphs(unittest.TestCase):
    def test_low_and_draining_is_red(self):
        glyph = power_glyph(laptop(20), "icon_percent", 20)
        self.assertEqual((glyph.tone, glyph.overlay), ("low", ""))
        self.assertEqual(power_glyph(laptop(21), "icon_percent", 20).tone, "normal")
        self.assertEqual(power_glyph(laptop(15, PLUGGED_IDLE, draining=True),
                                     "icon_percent", 20).tone, "low")

    def test_plugged_states_are_green_with_bolt_or_plug(self):
        self.assertEqual(power_glyph(laptop(15, CHARGING), "icon_percent", 20),
                         BatteryGlyph(15, "plugged", "bolt", False))
        for state in (PLUGGED_IDLE, FULL, LIMIT_REACHED):
            glyph = power_glyph(laptop(80, state), "icon_percent", 20)
            self.assertEqual((glyph.tone, glyph.overlay), ("plugged", "plug"))

    def test_percent_inside_only_for_icon_percent_time_and_no_icon_for_percent_only(self):
        self.assertTrue(power_glyph(laptop(), "icon_percent_time", 20).inline_percent)
        self.assertFalse(power_glyph(laptop(), "icon_time", 20).inline_percent)
        self.assertIsNone(power_glyph(laptop(), "percent_only", 20))
        self.assertIsNone(power_glyph(desktop(), "percent_only", 20))

    def test_desktop_plug_and_ups_on_battery(self):
        self.assertEqual(power_glyph(desktop(), "icon_percent", 20), DesktopGlyph(False, False, None))
        self.assertEqual(power_glyph(desktop(True, charge=64.0), "icon_only", 20),
                         DesktopGlyph(True, False, 64))
        self.assertTrue(power_glyph(desktop(True, low=True), "icon_only", 20).low)
        self.assertEqual(power_glyph(None, "icon_percent", 20), DesktopGlyph(False, False, None))


class Svg(unittest.TestCase):
    def test_rendering_is_deterministic_and_well_formed(self):
        glyph = BatteryGlyph(82, "normal", "", False)
        self.assertEqual(render(glyph), render(BatteryGlyph(82, "normal", "", False)))
        self.assertNotEqual(render(glyph), render(BatteryGlyph(83, "normal", "", False)))
        for item in (glyph, BatteryGlyph(5, "low", "", True), BatteryGlyph(None, "plugged", "plug", False),
                     DesktopGlyph(False, False, None), DesktopGlyph(True, True, 30)):
            ElementTree.fromstring(render(item))

    def test_battery_is_wide_enough_to_keep_its_aspect_in_the_panel(self):
        width, height = size(render(BatteryGlyph(50, "normal", "", False)))
        self.assertGreaterEqual(width, height * 1.5)
        inline_width, _ = size(render(BatteryGlyph(50, "normal", "", True)))
        self.assertGreater(inline_width, width)

    def test_colors(self):
        self.assertIn(theme.CRITICAL, render(BatteryGlyph(10, "low", "", False)))
        self.assertIn(theme.GOOD, render(BatteryGlyph(70, "plugged", "bolt", False)))
        self.assertNotIn(theme.GOOD, render(BatteryGlyph(70, "normal", "", False)))
        self.assertIn(theme.WARNING, render(DesktopGlyph(True, False, 80)))
        self.assertIn(theme.CRITICAL, render(DesktopGlyph(True, True, 20)))

    def test_overlays_and_inline_text(self):
        self.assertIn('id="bolt"', render(BatteryGlyph(70, "plugged", "bolt", False)))
        self.assertIn('id="plug"', render(BatteryGlyph(70, "plugged", "plug", False)))
        self.assertNotIn('id="bolt"', render(BatteryGlyph(70, "normal", "", False)))
        self.assertIn(">82%<", render(BatteryGlyph(82, "normal", "", True)))
        self.assertIn(">—<", render(BatteryGlyph(None, "normal", "", True)))

    def test_fill_follows_the_level(self):
        empty = render(BatteryGlyph(0, "low", "", False))
        full = render(BatteryGlyph(100, "normal", "", False))
        self.assertIn('id="level"', empty)
        self.assertNotEqual(empty, full)


class Writer(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.directory = self.base/"runtime/system-device-monitor/icons"
        self.writer = IconWriter(self.directory)

    def test_alternates_only_when_the_content_changes(self):
        first = self.writer.publish("<svg>1</svg>")
        self.assertEqual(first.name, "power-a.svg")
        inode = first.stat().st_ino
        self.assertEqual(self.writer.publish("<svg>1</svg>"), first)
        self.assertEqual(first.stat().st_ino, inode)
        second = self.writer.publish("<svg>2</svg>")
        self.assertEqual(second.name, "power-b.svg")
        self.assertEqual(self.writer.publish("<svg>3</svg>").name, "power-a.svg")
        self.assertEqual((self.directory/"power-a.svg").read_text(), "<svg>3</svg>")

    def test_private_permissions(self):
        path = self.writer.publish("<svg/>")
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_symlinked_directory_is_refused(self):
        target = self.base/"elsewhere"
        target.mkdir()
        self.directory.parent.mkdir(parents=True)
        os.symlink(target, self.directory)
        with self.assertLogs("sdm.ui.tray_icons", "WARNING"):
            self.assertIsNone(IconWriter(self.directory).publish("<svg/>"))
        self.assertEqual(list(target.iterdir()), [])

    def test_write_failure_returns_none_and_retries_later(self):
        self.directory.parent.mkdir(parents=True)
        self.directory.parent.chmod(0o500)
        self.addCleanup(self.directory.parent.chmod, 0o700)
        with self.assertLogs("sdm.ui.tray_icons", "WARNING"):
            self.assertIsNone(self.writer.publish("<svg/>"))
        self.directory.parent.chmod(0o700)
        self.assertEqual(self.writer.publish("<svg/>").name, "power-a.svg")


if __name__ == "__main__":
    unittest.main()
