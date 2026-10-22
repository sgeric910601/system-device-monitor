"""頂端列「耗電」指示器與選單第一行；不建立真正的 GTK 元件。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm import paths
from sdm.core.settings import Preferences
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService
from sdm.ui import app as ui_app
from sdm.ui.power_tray import DESKTOP_ICON

ACTIVE = ui_app.AyatanaAppIndicator3.IndicatorStatus.ACTIVE
PASSIVE = ui_app.AyatanaAppIndicator3.IndicatorStatus.PASSIVE


class FakeIndicator:
    def __init__(self): self.label, self.status = None, None
    def set_label(self, label, guide): self.label = label
    def set_status(self, status): self.status = status


class FakeItem:
    def __init__(self, label=""): self.label, self.sets = label, 0
    def get_label(self): return self.label
    def set_label(self, label): self.label, self.sets = label, self.sets + 1


def snapshot(load_w=288.0):
    ups = UpsReading(status=("ONLINE",), online=True, load_w=load_w)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0).sample()


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.prefs_path = Path(temp.name)/"preferences.json"
        self.legacy_path = Path(temp.name)/"settings.json"
        guard = patch.multiple(paths, PREFERENCES=self.prefs_path, CONFIG=self.legacy_path)
        guard.start()
        self.addCleanup(guard.stop)
        self.monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        self.monitor.preferences = Preferences.defaults()
        self.monitor.power_indicator = FakeIndicator()
        self.monitor.power_item = FakeItem()
        self.rendered = []
        self.monitor.render_indicator = lambda: self.rendered.append(True)


class Indicator(Base):
    def test_label_and_menu_line(self):
        self.monitor.render_power_tray(snapshot())
        self.assertEqual(self.monitor.power_indicator.label, "288 W")
        self.assertEqual(self.monitor.power_indicator.status, ACTIVE)
        self.assertEqual(self.monitor.power_item.label, "耗電：288 W（3 分鐘平均 288 W）")

    def test_hidden_when_turned_off(self):
        self.monitor.preferences = Preferences.defaults().with_changes(tray_power=False)
        self.monitor.render_power_tray(snapshot())
        self.assertEqual(self.monitor.power_indicator.status, PASSIVE)

    def test_menu_label_is_only_set_when_changed(self):
        self.monitor.render_power_tray(snapshot())
        self.monitor.render_power_tray(snapshot())
        self.assertEqual(self.monitor.power_item.sets, 1)

    def test_before_the_first_reading(self):
        self.monitor.render_power_tray(None)
        self.assertEqual(self.monitor.power_indicator.label, "— W")
        self.assertEqual(self.monitor.power_item.label, "耗電：讀取中")


class IconIndicator(FakeIndicator):
    def __init__(self):
        super().__init__()
        self.icons = []
    def set_icon_full(self, name, description): self.icons.append((name, description))


def laptop_snapshot(percent=82, status="Discharging"):
    battery = BatteryReading(name="BAT0", status=status, capacity=percent, power_w=15.0)
    return PowerService(read_ups=lambda: None, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0,
                        read_batteries=lambda: (battery,)).sample()


class LaptopIndicator(Base):
    def setUp(self):
        super().setUp()
        self.monitor.power_indicator = IconIndicator()

    def test_percent_battery_icon_and_menu_line(self):
        self.monitor.render_power_tray(laptop_snapshot())
        indicator = self.monitor.power_indicator
        self.assertEqual((indicator.label, indicator.status), ("82%", ACTIVE))
        self.assertEqual(indicator.icons, [("battery-level-80-symbolic", "82%")])
        self.assertEqual(self.monitor.power_item.label, "電池：82%・計算中…")

    def test_icon_is_only_replaced_when_it_changes_and_desktop_restores_it(self):
        self.monitor.render_power_tray(laptop_snapshot())
        self.monitor.render_power_tray(laptop_snapshot(84))
        self.assertEqual(len(self.monitor.power_indicator.icons), 1)
        self.monitor.render_power_tray(snapshot())
        self.assertEqual(self.monitor.power_indicator.icons[-1][0], DESKTOP_ICON)
        self.assertEqual(self.monitor.power_indicator.label, "288 W")


if __name__ == "__main__":
    unittest.main()
