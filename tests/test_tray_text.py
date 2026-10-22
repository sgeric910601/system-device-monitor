"""頂端列文字：五種顯示格式 × 電池狀態（參考專案 updateStatusItem），以及分開／合併兩種模式。"""
import unittest

from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              BatterySnapshot)
from sdm.core.estimates import Estimates
from sdm.core.power_breakdown import Breakdown
from sdm.core.settings import LEGACY_DEFAULTS, Preferences
from sdm.data.gpu import GpuReading
from sdm.data.ups import UpsReading
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import DesktopPower
from sdm.ui import tray_text
from sdm.ui.tray_text import (compact_rate, enabled_items, merged_label, power_label,
                              tray_view)

MODES = ("icon_percent", "icon_only", "percent_only", "icon_time", "icon_percent_time")


def laptop(percent=82, state=DISCHARGING, to_empty=None, to_full=None, draining=False):
    estimates = Estimates(state=state, system_to_empty=to_empty, to_full=to_full,
                          draining_on_ac=draining)
    return LaptopPower(stamp=0.0, battery=BatterySnapshot(percent=percent),
                       estimates=estimates, breakdown=Breakdown())


def desktop(watts=297.0):
    return DesktopPower(stamp=0.0, system_w=watts, average_w=None, source="ups", supply="mains",
                        breakdown=Breakdown(), ups=UpsReading(online=True), gpus=())


FAST = dict(cpu=12.4, ram=34.0, rx=340 * 1024, tx=12 * 1024)
SLOW = dict(cpu_temp=54.2, gpu=GpuReading(vendor="nvidia", index=0, name="GPU", usage=30.0))


class DisplayModes(unittest.TestCase):
    """（快照、各模式的文字）：icon_percent、icon_only、percent_only、icon_time、icon_percent_time。"""

    CASES = (
        ("discharging", laptop(to_empty=205), ("82%", "", "82%", "3:25", "3:25")),
        ("discharging without estimate", laptop(), ("82%", "", "82%", "82%", "—")),
        ("charging", laptop(55, CHARGING, to_full=36), ("55%", "", "⚡55%", "0:36", "0:36")),
        ("charging without estimate", laptop(55, CHARGING), ("55%", "", "⚡55%", "55%", "—")),
        ("full", laptop(100, FULL), ("100%", "", "100%", "100%", "已充飽")),
        ("plugged", laptop(80, PLUGGED_IDLE), ("80%", "", "80%", "80%", "接電")),
        ("draining on AC", laptop(40, PLUGGED_IDLE, to_empty=90, draining=True),
         ("40%", "", "40%", "1:30", "1:30")),
        ("limit", laptop(80, LIMIT_REACHED), ("80%", "", "80%", "80%", "已達上限")),
        ("no percent", laptop(None), ("—%", "", "—%", "—%", "—")),
        ("desktop", desktop(), ("297 W", "", "297 W", "297 W", "297 W")),
        ("desktop before reading", None, ("— W", "", "— W", "— W", "— W")),
    )

    def test_every_mode_and_state(self):
        for name, power, expected in self.CASES:
            for mode, text in zip(MODES, expected):
                with self.subTest(name, mode=mode):
                    self.assertEqual(power_label(power, mode), text)


class Merged(unittest.TestCase):
    def test_laptop_power_part_adds_the_time_in_brackets(self):
        self.assertEqual(tray_text.merged_power(laptop(to_empty=205), "icon_time"), "82%（3:25）")
        self.assertEqual(tray_text.merged_power(laptop(100, FULL), "icon_percent_time"),
                         "100%（已充飽）")
        self.assertEqual(tray_text.merged_power(laptop(), "icon_time"), "82%")
        self.assertEqual(tray_text.merged_power(laptop(), "icon_percent"), "82%")
        self.assertEqual(tray_text.merged_power(laptop(55, CHARGING), "percent_only"), "⚡55%")
        self.assertEqual(tray_text.merged_power(laptop(), "icon_only"), "")
        self.assertEqual(tray_text.merged_power(desktop(), "icon_time"), "297 W")

    def test_label_in_fixed_order(self):
        view = tray_view(desktop(), FAST, SLOW, LEGACY_DEFAULTS,
                         Preferences.defaults().with_changes(tray_mode="merged"))
        self.assertEqual(view.merged_label, "297 W｜CPU 12%｜54°C｜RAM 34%｜GPU 30%｜↓340K ↑12K")

    def test_laptop_example(self):
        legacy = dict(LEGACY_DEFAULTS, temperature=False, ram=False, gpu=False, network=False)
        prefs = Preferences.defaults().with_changes(tray_mode="merged", tray_display="icon_time")
        view = tray_view(laptop(to_empty=205), FAST, SLOW, legacy, prefs)
        self.assertEqual(view.merged_label, "82%（3:25）｜CPU 12%")

    def test_empty_parts_are_skipped(self):
        self.assertEqual(merged_label(("", "CPU 12%", "")), "CPU 12%")
        self.assertEqual(merged_label(()), "")

    def test_compact_rates(self):
        self.assertEqual([compact_rate(value) for value in
                          (None, 0, 300, 340 * 1024, 1.25 * 1024 ** 2, 34 * 1024 ** 2)],
                         ["—", "0K", "0K", "340K", "1.2M", "34M"])


class Items(unittest.TestCase):
    def test_power_first_then_the_legacy_order(self):
        self.assertEqual(enabled_items(LEGACY_DEFAULTS, Preferences.defaults()),
                         ("power", "cpu", "temperature", "ram", "gpu", "network"))

    def test_turned_off_items_and_modules_are_left_out(self):
        legacy = dict(LEGACY_DEFAULTS, gpu=False)
        prefs = Preferences.defaults().with_changes(tray_power=False, monitor_memory=False)
        self.assertEqual(enabled_items(legacy, prefs), ("cpu", "temperature", "network"))

    def test_master_switch_leaves_only_power(self):
        prefs = Preferences.defaults().with_changes(system_monitors=False)
        self.assertEqual(enabled_items(LEGACY_DEFAULTS, prefs), ("power",))

    def test_split_view(self):
        view = tray_view(laptop(to_empty=205), FAST, SLOW, LEGACY_DEFAULTS,
                         Preferences.defaults().with_changes(tray_display="icon_time"))
        self.assertEqual(view.mode, "split")
        self.assertEqual(view.labels["power"], "3:25")
        self.assertEqual(view.labels["network"], "↓340.0 KiB/s ↑12.0 KiB/s")
        self.assertEqual(view.visible, ("power", "cpu", "temperature", "ram", "gpu", "network"))
        self.assertIsNotNone(view.glyph)

    def test_menu_lists_every_item_of_enabled_modules(self):
        prefs = Preferences.defaults().with_changes(monitor_network=False)
        view = tray_view(desktop(), FAST, SLOW, dict(LEGACY_DEFAULTS, cpu=False), prefs)
        keys = [key for key, _, _ in view.menu]
        self.assertEqual(keys, ["power", "cpu", "temperature", "ram", "gpu", "ups"])
        lines = {key: (text, page) for key, text, page in view.menu}
        self.assertEqual(lines["cpu"], ("CPU 12%", "cpu"))
        self.assertEqual(lines["temperature"], ("CPU 溫度 54°C", "cpu"))
        self.assertEqual(lines["power"][1], "power")
        self.assertEqual(lines["ups"][1], "power")


if __name__ == "__main__":
    unittest.main()
