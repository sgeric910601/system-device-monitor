"""頂端列控制：分開／合併兩種模式、指示器只建立一次並重複使用、耗電圖示與退回圖示。

不建立真正的 AppIndicator：以假的工廠記錄每個指示器收到的設定。
"""
from pathlib import Path
import tempfile
import unittest

from sdm.core.settings import LEGACY_DEFAULTS, Preferences
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService
from sdm.ui.power_tray import DESKTOP_ICON
from sdm.ui.tray import ACTIVE, APP_ICON, ITEM_ICONS, PASSIVE, Tray
from sdm.ui.tray_icons import IconWriter
from sdm.ui.tray_text import tray_view

FAST = dict(cpu=12.0, ram=34.0, rx=1024.0, tx=1024.0)


class FakeIndicator:
    def __init__(self, key, icon):
        self.key, self.icon, self.label, self.status = key, icon, None, None
        self.calls = []

    def set_label(self, label, guide):
        self.label = label
        self.calls.append("label")

    def set_icon_full(self, icon, description):
        self.icon = icon
        self.calls.append("icon")

    def set_status(self, status):
        self.status = status
        self.calls.append("status")


class FakeFactory:
    def __init__(self):
        self.created = []

    def create(self, key, icon):
        indicator = FakeIndicator(key, icon)
        self.created.append(indicator)
        return indicator


def desktop(load_w=288.0):
    ups = UpsReading(status=("ONLINE",), online=True, load_w=load_w)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0).sample()


def laptop(percent=82):
    battery = BatteryReading(name="BAT0", status="Discharging", capacity=percent, power_w=15.0)
    return PowerService(read_ups=lambda: None, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0,
                        read_batteries=lambda: (battery,)).sample()


class Modes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.factory = FakeFactory()
        self.tray = Tray(self.factory, IconWriter(Path(temp.name)/"icons"))

    def view(self, power=None, legacy=LEGACY_DEFAULTS, **prefs):
        return tray_view(desktop() if power is None else power, FAST, {}, legacy,
                         Preferences.defaults().with_changes(**prefs))

    def active(self):
        return {indicator.key: indicator for indicator in self.factory.created
                if indicator.status == ACTIVE}

    def test_split_shows_power_and_the_legacy_items(self):
        self.tray.apply(self.view())
        active = self.active()
        self.assertEqual(sorted(active, key=str),
                         sorted(["power", "cpu", "temperature", "ram", "gpu", "network"]))
        self.assertEqual(active["power"].label, "288 W")
        self.assertTrue(active["power"].icon.endswith("power-a.svg"))
        self.assertEqual(active["cpu"].icon, ITEM_ICONS["cpu"])

    def test_merged_uses_one_indicator(self):
        self.tray.apply(self.view())
        self.tray.apply(self.view(tray_mode="merged"))
        active = self.active()
        self.assertEqual(list(active), ["power"])
        self.assertTrue(active["power"].label.startswith("288 W｜CPU 12%"))
        self.assertEqual(sum(1 for i in self.factory.created if i.status == PASSIVE), 5)

    def test_switching_back_and_forth_reuses_the_indicators(self):
        for mode in ("split", "merged") * 5:
            self.tray.apply(self.view(tray_mode=mode))
        self.assertEqual(len(self.factory.created), 6)

    def test_merged_mode_creates_only_what_it_needs(self):
        self.tray.apply(self.view(tray_mode="merged"))
        self.assertEqual([i.key for i in self.factory.created], ["power"])

    def test_nothing_enabled_shows_the_app_icon(self):
        legacy = {key: False for key in LEGACY_DEFAULTS}
        self.tray.apply(self.view(legacy=legacy, tray_power=False))
        self.assertEqual(list(self.active()), [None])
        self.assertEqual(self.active()[None].icon, APP_ICON)
        self.tray.apply(self.view())
        self.assertNotIn(None, self.active())

    def test_merged_without_the_power_item_uses_the_app_icon(self):
        self.tray.apply(self.view(tray_mode="merged", tray_power=False))
        self.assertEqual(self.active()["power"].icon, APP_ICON)
        self.assertTrue(self.active()["power"].label.startswith("CPU 12%"))

    def test_unchanged_values_are_not_sent_again(self):
        self.tray.apply(self.view())
        power = self.active()["power"]
        count = len(power.calls)
        self.tray.apply(self.view())
        self.assertEqual(len(power.calls), count)

    def test_icon_file_changes_only_with_the_content(self):
        self.tray.apply(self.view(power=laptop(82)))
        first = self.active()["power"].icon
        self.tray.apply(self.view(power=laptop(82)))
        self.assertEqual(self.active()["power"].icon, first)
        self.tray.apply(self.view(power=laptop(40)))
        self.assertNotEqual(self.active()["power"].icon, first)

    def test_unwritable_icon_falls_back_to_the_theme_icon(self):
        self.tray.writer = IconWriter(Path("/proc/forbidden/icons"))
        with self.assertLogs("sdm.ui.tray_icons", "WARNING"):
            self.tray.apply(self.view())
        self.assertEqual(self.active()["power"].icon, DESKTOP_ICON)
        self.tray.apply(self.view(power=laptop()))
        self.assertEqual(self.active()["power"].icon, "battery-level-80-symbolic")

    def test_hide_all_on_exit(self):
        self.tray.apply(self.view())
        self.tray.hide_all()
        self.assertEqual(self.active(), {})
        self.assertEqual(self.tray.count(), 6)


if __name__ == "__main__":
    unittest.main()
