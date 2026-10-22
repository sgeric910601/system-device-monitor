"""sysfs power_supply 讀取：各廠牌假目錄、壞值、周邊電池與不讀序號。"""
import dataclasses
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm.data import power_supply
from sdm.data.power_supply import (AdapterReading, BatteryReading, has_system_battery,
                                   read_adapters, read_batteries)
from tests.fixtures import sysfs


class Tree(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def only(self):
        batteries = read_batteries(self.root)
        self.assertEqual(len(batteries), 1)
        return batteries[0]


class ThinkPad(Tree):
    def test_energy_style_battery(self):
        sysfs.thinkpad(self.root)
        battery = self.only()
        self.assertEqual((battery.name, battery.status, battery.capacity), ("BAT0", "Discharging", 82))
        self.assertEqual((battery.energy_now_wh, battery.energy_full_wh, battery.energy_design_wh),
                         (41.0, 50.0, 55.0))
        self.assertIsNone(battery.charge_now_ah)
        self.assertEqual((battery.power_w, battery.voltage_v, battery.voltage_min_design_v),
                         (15.0, 12.45, 11.55))
        self.assertIsNone(battery.current_a)
        self.assertEqual((battery.cycle_count, battery.temperature_c), (212, 31.4))
        self.assertEqual((battery.start_threshold, battery.end_threshold), (75, 80))
        self.assertTrue(battery.thresholds_supported)
        self.assertEqual(battery.behaviour, "auto")
        self.assertEqual((battery.technology, battery.manufacturer, battery.model),
                         ("Li-poly", "Example Cells", "Example 45N1"))
        self.assertEqual(battery.capacity_level, "Normal")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            battery.capacity = 1

    def test_mains_adapter(self):
        sysfs.thinkpad(self.root, online=1)
        self.assertEqual(read_adapters(self.root),
                         (AdapterReading(name="AC", kind="Mains", online=True),))
        self.assertIsNone(read_adapters(self.root)[0].negotiated_w)

    def test_serial_number_and_uevent_are_never_opened(self):
        sysfs.thinkpad(self.root)
        sysfs.peripheral(self.root)
        opened = []
        original = power_supply.read

        def spy(path, default=""):
            opened.append(Path(path).name)
            return original(path, default)

        with patch.object(power_supply, "read", side_effect=spy):
            read_batteries(self.root)
            read_adapters(self.root)
            has_system_battery(self.root)
        self.assertTrue(opened)
        self.assertFalse({"serial_number", "uevent"} & set(opened))


class Asus(Tree):
    def test_charge_style_battery_reports_magnitudes(self):
        sysfs.asus(self.root)
        battery = self.only()
        self.assertEqual((battery.status, battery.capacity), ("Charging", 64))
        self.assertEqual((battery.charge_now_ah, battery.charge_full_ah, battery.charge_design_ah),
                         (2.688, 4.2, 4.8))
        self.assertIsNone(battery.energy_now_wh)
        # 驅動對電流正負號的定義不一致：只保留大小，方向由狀態決定
        self.assertEqual(battery.current_a, 2.1)
        self.assertIsNone(battery.power_w)
        self.assertIsNone(battery.cycle_count)      # 0 代表未知
        self.assertEqual((battery.start_threshold, battery.end_threshold), (None, 80))
        self.assertEqual(battery.name, "BAT1")


class Framework(Tree):
    def test_usb_pd_adapter_negotiated_and_rated(self):
        sysfs.framework(self.root)
        adapters = read_adapters(self.root)
        self.assertEqual([adapter.name for adapter in adapters],
                         ["ACAD", "ucsi-source-psy-USBC000:001", "ucsi-source-psy-USBC000:002"])
        pd = adapters[1]
        self.assertEqual((pd.kind, pd.online, pd.voltage_v, pd.current_a), ("USB", True, 20.0, 2.25))
        # ucsi 的電壓 × 電流是協商的契約功率，不是實際輸入
        self.assertEqual((pd.negotiated_w, pd.rated_w), (45.0, 65.0))
        self.assertFalse(adapters[2].online)

    def test_battery(self):
        sysfs.framework(self.root)
        battery = self.only()
        self.assertEqual((battery.current_a, battery.voltage_v, battery.end_threshold),
                         (1.5, 16.8, 90))

    def test_plain_usb_port_is_not_an_adapter(self):
        sysfs.supply(self.root, "usb-port", type="USB", online=1, usb_type="[SDP] DCP CDP")
        sysfs.supply(self.root, "ucsi-source-psy-USBC000:003", type="USB", online=1,
                     usb_type="[C] PD")
        self.assertEqual([adapter.name for adapter in read_adapters(self.root)],
                         ["ucsi-source-psy-USBC000:003"])

    def test_input_power_limit_is_the_rating_when_no_maximums(self):
        sysfs.supply(self.root, "ADP0", type="Mains", online=1, input_power_limit=65_000_000)
        self.assertEqual(read_adapters(self.root)[0].rated_w, 65.0)


class OtherStyles(Tree):
    def test_without_thresholds(self):
        sysfs.no_thresholds(self.root)
        battery = self.only()
        self.assertEqual(battery.name, "CMB0")
        self.assertFalse(battery.thresholds_supported)
        self.assertEqual((battery.start_threshold, battery.end_threshold), (None, None))

    def test_not_charging_at_the_end_threshold(self):
        sysfs.not_charging_at_limit(self.root)
        battery = self.only()
        self.assertEqual((battery.name, battery.status, battery.power_w), ("BATT", "Not charging", 0.0))

    def test_unknown_status(self):
        sysfs.unknown_status(self.root)
        self.assertEqual(self.only().status, "Unknown")

    def test_two_batteries_sorted_by_name(self):
        sysfs.two_batteries(self.root)
        self.assertEqual([battery.name for battery in read_batteries(self.root)], ["BAT0", "BAT1"])

    def test_peripheral_battery_is_ignored(self):
        sysfs.peripheral(self.root)
        self.assertEqual(read_batteries(self.root), ())
        self.assertFalse(has_system_battery(self.root))
        sysfs.thinkpad(self.root)
        self.assertEqual([battery.name for battery in read_batteries(self.root)], ["BAT0"])
        self.assertTrue(has_system_battery(self.root))

    def test_empty_battery_slot_is_ignored(self):
        sysfs.absent_slot(self.root)
        self.assertEqual(read_batteries(self.root), ())

    def test_missing_directory(self):
        self.assertEqual(read_batteries(self.root), ())
        self.assertEqual(read_adapters(self.root), ())
        self.assertFalse(has_system_battery(self.root))


class Garbage(Tree):
    def test_invalid_values_become_unknown(self):
        sysfs.garbage(self.root)
        battery = self.only()
        self.assertEqual(battery, BatteryReading(
            name="BAT0", status="Discharging", manufacturer=battery.manufacturer))
        self.assertEqual(len(battery.manufacturer), power_supply.Battery.TEXT_MAX_CHARS)
        self.assertTrue(battery.manufacturer.startswith("Bad Vendor"))
        self.assertEqual(read_adapters(self.root),
                         (AdapterReading(name="AC", kind="Mains", online=False),))

    def test_unreadable_attribute_does_not_raise(self):
        sysfs.thinkpad(self.root)
        (self.root/sysfs.SUPPLY_DIR/"BAT0"/"capacity").unlink()
        (self.root/sysfs.SUPPLY_DIR/"BAT0"/"capacity").mkdir()
        self.assertIsNone(self.only().capacity)

    def test_zero_full_capacity_is_unknown(self):
        sysfs.thinkpad(self.root, energy_full=0, energy_full_design=0)
        battery = self.only()
        self.assertEqual((battery.energy_full_wh, battery.energy_design_wh), (None, None))
        self.assertEqual(battery.energy_now_wh, 41.0)

    def test_values_beyond_the_plausible_range(self):
        sysfs.thinkpad(self.root, power_now=900_000_000, voltage_now=80_000_000,
                       energy_now=2_000_000_000)
        battery = self.only()
        self.assertEqual((battery.power_w, battery.voltage_v, battery.energy_now_wh),
                         (None, None, None))


class Behaviour(unittest.TestCase):
    def test_active_choice_is_the_bracketed_one(self):
        parse = power_supply.active_choice
        self.assertEqual(parse("auto [inhibit-charge] force-discharge"), "inhibit-charge")
        self.assertEqual(parse("auto"), "auto")
        self.assertEqual(parse(""), "")
        self.assertEqual(parse("auto inhibit-charge"), "")


if __name__ == "__main__":
    unittest.main()
