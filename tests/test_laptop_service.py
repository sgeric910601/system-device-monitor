"""電源服務的筆電模式：有系統電池才產生筆電快照，桌機（只有周邊電池）維持原本的快照。"""
import dataclasses
import math
from pathlib import Path
import tempfile
import unittest

from sdm.core.battery import CHARGING, DISCHARGING, LIMIT_REACHED, PLUGGED_IDLE
from sdm.core.presentation import FLOW_BATTERY, FLOW_MIXED
from sdm.data.power_supply import read_adapters, read_batteries
from sdm.data.ups import UpsReading
from sdm.data.upower import UpowerReading
from sdm.services.battery_service import LaptopPower, laptop_history_sample
from sdm.services.power_service import DesktopPower, PowerService, history_sample
from tests.fixtures import sysfs

UPS = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_w=288.0)
ESTIMATING = UpowerReading(on_battery=True, is_battery=True)


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock, self.wall = Clock(100.0), Clock(1_800_000_000.0)
        self.upower, self.profile, self.ups = None, None, None
        self.service = PowerService(
            read_ups=lambda: self.ups, read_gpus=lambda: (), read_uptime=lambda: 3600.0,
            processor="Example CPU", model="Example Laptop", clock=self.clock, wall=self.wall,
            read_batteries=lambda: read_batteries(self.root),
            read_adapters=lambda: read_adapters(self.root),
            read_upower=lambda: self.upower, read_profile=lambda: self.profile)

    def advance(self, seconds):
        self.clock.value += seconds
        self.wall.value += seconds

    def battery_dir(self, name):
        return self.root/sysfs.SUPPLY_DIR/name

    def set_attr(self, supply, **values):
        for key, value in values.items():
            (self.battery_dir(supply)/key).write_text(f"{value}\n")


class Mode(Base):
    def test_desktop_with_only_a_peripheral_battery_keeps_the_desktop_snapshot(self):
        sysfs.peripheral(self.root)
        self.ups = UPS
        power = self.service.sample()
        self.assertIsInstance(power, DesktopPower)
        self.assertEqual((power.system_w, power.source), (288.0, "ups"))
        sample = history_sample(power)
        self.assertEqual((sample.pct, sample.watts, sample.ups_pct), (None, 288.0, 100.0))

    def test_laptop_snapshot(self):
        sysfs.thinkpad(self.root)
        self.upower = UpowerReading(on_battery=True, is_battery=True, time_to_empty_s=12300)
        self.profile = "power-saver"
        power = self.service.sample()
        self.assertIsInstance(power, LaptopPower)
        self.assertEqual((power.percent, power.state, power.system_w), (82, DISCHARGING, 15.0))
        self.assertEqual(power.estimates.system_to_empty, 205)
        self.assertEqual(power.estimates.flow_kind, FLOW_BATTERY)
        self.assertEqual((power.profile, power.processor, power.model, power.uptime_s),
                         ("power-saver", "Example CPU", "Example Laptop", 3600.0))
        self.assertEqual(power.stamp, 1_800_000_000.0)
        self.assertEqual(power.breakdown.status, "incomplete")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            power.stamp = 1.0

    def test_sequence_orders_snapshots_from_both_modes(self):
        sysfs.peripheral(self.root)
        first = self.service.sample()
        sysfs.thinkpad(self.root)
        second = self.service.sample()
        self.assertLess(first.sequence, second.sequence)

    def test_upower_still_estimating(self):
        sysfs.thinkpad(self.root)
        self.upower = ESTIMATING
        power = self.service.sample()
        self.assertIsNone(power.estimates.system_to_empty)
        self.assertTrue(power.estimates.system_reports)


class History(Base):
    def test_on_battery_row(self):
        sysfs.thinkpad(self.root)
        sample = history_sample(self.service.sample())
        self.assertEqual((sample.t, sample.pct, sample.watts, sample.plugged, sample.charging),
                         (1_800_000_000.0, 82, 15.0, False, False))
        self.assertEqual((sample.ups_pct, sample.ups_online), (None, None))

    def test_unknown_system_power_while_charging(self):
        sysfs.thinkpad(self.root, online=1, status="Charging", capacity=60, power_now=20_000_000)
        sample = history_sample(self.service.sample())
        self.assertEqual((sample.pct, sample.watts, sample.plugged, sample.charging),
                         (60, None, True, True))

    def test_unknown_percent_is_not_recorded(self):
        sysfs.garbage(self.root)
        self.assertIsNone(laptop_history_sample(self.service.sample()))
        self.assertIsNone(history_sample(None))


class Tracking(Base):
    def test_unplug_and_plug_times(self):
        sysfs.thinkpad(self.root, online=1, status="Charging", power_now=20_000_000)
        self.assertIsNone(self.service.sample().unplugged_at)
        self.advance(60)
        self.set_attr("AC", online=0)
        self.set_attr("BAT0", status="Discharging", power_now=12_000_000)
        power = self.service.sample()
        self.assertEqual((power.state, power.unplugged_at), (DISCHARGING, self.wall.value))
        unplugged = power.unplugged_at
        self.advance(60)
        self.assertEqual(self.service.sample().unplugged_at, unplugged)
        self.advance(60)
        self.set_attr("AC", online=1)
        power = self.service.sample()
        self.assertEqual((power.unplugged_at, power.plugged_in_at), (None, self.wall.value))

    def test_started_on_battery_has_no_live_unplug_time(self):
        sysfs.thinkpad(self.root)
        self.assertIsNone(self.service.sample().unplugged_at)

    def test_weak_adapter_is_reported_after_thirty_seconds(self):
        sysfs.framework(self.root, status="Discharging", current_now=600_000)
        self.set_attr("ucsi-source-psy-USBC000:001", current_now=250_000)
        self.set_attr("ACAD", online=0)
        self.set_attr("ucsi-source-psy-USBC000:001", online=0)
        self.service.sample()
        self.advance(5)
        self.set_attr("ucsi-source-psy-USBC000:001", online=1)
        power = self.service.sample()
        self.assertEqual(power.state, PLUGGED_IDLE)
        self.assertFalse(power.estimates.draining_on_ac)
        self.advance(30)
        power = self.service.sample()
        self.assertTrue(power.estimates.draining_on_ac)
        self.assertAlmostEqual(power.estimates.battery_w, -0.6 * 16.8)
        self.assertAlmostEqual(power.system_w, 5.0 + 0.6 * 16.8)
        self.assertIsNotNone(power.estimates.live_to_empty)
        self.assertEqual(power.estimates.flow_kind, FLOW_MIXED)

    def test_average_follows_the_three_minute_ema(self):
        sysfs.thinkpad(self.root)
        self.assertEqual(self.service.sample().estimates.averages.discharge, 15.0)
        self.advance(180)
        self.set_attr("BAT0", power_now=5_000_000)
        expected = 15 + (1 - math.exp(-1)) * (5 - 15)
        self.assertAlmostEqual(self.service.sample().estimates.averages.discharge, expected)

    def test_limit_reached_display(self):
        sysfs.not_charging_at_limit(self.root)
        power = self.service.sample()
        self.assertEqual((power.state, power.battery.limit), (LIMIT_REACHED, 80))

    def test_charging_display_needs_ten_seconds_after_idle(self):
        sysfs.thinkpad(self.root, online=1, status="Not charging", capacity=70,
                       charge_control_end_threshold=100, power_now=0)
        self.assertEqual(self.service.sample().state, PLUGGED_IDLE)
        self.set_attr("BAT0", status="Charging", power_now=25_000_000)
        self.advance(5)
        self.assertEqual(self.service.sample().state, PLUGGED_IDLE)
        self.advance(10)
        self.assertEqual(self.service.sample().state, CHARGING)

    def test_returning_to_a_desktop_resets_the_tracker(self):
        sysfs.thinkpad(self.root)
        self.service.sample()
        for name in ("BAT0", "AC"):
            for child in self.battery_dir(name).iterdir():
                child.unlink()
            self.battery_dir(name).rmdir()
        self.assertIsInstance(self.service.sample(), DesktopPower)
        self.assertIsNone(self.service.tracker)


if __name__ == "__main__":
    unittest.main()
