"""電源服務的筆電模式：有系統電池才產生筆電快照，桌機（只有周邊電池）維持原本的快照。"""
import dataclasses
import math
from pathlib import Path
import tempfile
import unittest

from sdm.constants import Battery
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


class Locking(Base):
    def test_slow_readers_run_without_holding_the_service_lock(self):
        held = []

        def watch(value):
            held.append(self.service.lock.locked())
            return value

        self.service.read_upower = lambda: watch(None)
        self.service.read_profile = lambda: watch(None)
        self.service.read_ups = lambda: watch(None)
        self.service.read_gpus = lambda: watch(())
        self.service.read_adapters = lambda: watch(())
        sysfs.thinkpad(self.root)
        self.service.sample()
        sysfs_dir = self.root/sysfs.SUPPLY_DIR
        for name in ("BAT0", "AC"):
            for child in (sysfs_dir/name).iterdir():
                child.unlink()
            (sysfs_dir/name).rmdir()
        for _ in range(3):
            self.service.sample()       # 第三次切回桌機，讀不斷電系統與顯示卡
        self.assertTrue(held)
        self.assertFalse(any(held))


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

    def test_upower_state_is_passed_to_the_snapshot(self):
        sysfs.unknown_status(self.root, power_now=15_000_000)
        self.upower = UpowerReading(on_battery=False, is_battery=True, state=1)
        power = self.service.sample()
        self.assertEqual((power.battery.battery_w, power.state), (15.0, CHARGING))
        self.upower = UpowerReading(on_battery=False, is_battery=False, state=1)
        self.assertEqual(self.service.sample().battery.battery_w, 0.0)

    def test_charge_time_becomes_approximate_after_two_minutes_of_zero(self):
        sysfs.asus(self.root, charge_control_end_threshold=100)
        self.upower = UpowerReading(on_battery=False, is_battery=True, state=1)
        self.assertIsNone(self.service.sample().estimates.to_full)
        self.advance(Battery.UPOWER_STALL_SECONDS - 5)
        self.assertIsNone(self.service.sample().estimates.to_full)
        self.advance(5)
        estimates = self.service.sample().estimates
        self.assertTrue(estimates.to_full_approx)
        self.assertIsNotNone(estimates.to_full)

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
        self.assertIsNone(power.system_w)        # 協商功率不是實際輸入
        self.assertEqual(power.battery.adapter_negotiated_w, 5.0)
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

    def remove_supplies(self):
        for name in ("BAT0", "AC"):
            for child in self.battery_dir(name).iterdir():
                child.unlink()
            self.battery_dir(name).rmdir()

    def test_one_empty_read_keeps_laptop_mode_and_its_state(self):
        sysfs.thinkpad(self.root, online=0)
        self.service.sample()
        self.set_attr("AC", online=1)
        self.advance(5)
        before = self.service.sample()
        tracker = self.service.tracker
        self.assertIsNotNone(before.plugged_in_at)
        self.remove_supplies()
        self.advance(5)
        kept = self.service.sample()
        self.assertIsInstance(kept, LaptopPower)
        self.assertGreater(kept.sequence, before.sequence)
        self.assertEqual((kept.stamp, kept.percent), (before.stamp, before.percent))
        self.assertIs(self.service.tracker, tracker)
        self.assertEqual(tracker.plugged_in_at, before.plugged_in_at)
        self.assertEqual(tracker.averages.discharge, before.estimates.averages.discharge)
        sysfs.thinkpad(self.root, online=1)
        self.advance(5)
        self.assertEqual(self.service.sample().plugged_in_at, before.plugged_in_at)

    def test_three_empty_reads_return_to_a_desktop_and_reset_the_tracker(self):
        sysfs.thinkpad(self.root)
        self.service.sample()
        self.remove_supplies()
        self.assertIsInstance(self.service.sample(), LaptopPower)
        self.assertIsInstance(self.service.sample(), LaptopPower)
        self.assertIsInstance(self.service.sample(), DesktopPower)
        self.assertIsNone(self.service.tracker)


if __name__ == "__main__":
    unittest.main()
