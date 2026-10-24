"""充電上限的唯讀讀取：可調整的系統電池、目前的門檻與保留的狀態檔（假的 /sys）。"""
import json
import os
from pathlib import Path
import tempfile
import unittest

from sdm.data.charge_thresholds import (SavedLimit, Thresholds, find_battery, read_saved,
                                        read_thresholds)
from tests.fixtures.sysfs import asus, no_thresholds, supply, thinkpad


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)


class FindBattery(Base):
    def test_thinkpad_has_both_thresholds(self):
        thinkpad(self.root)
        self.assertEqual(find_battery(self.root), Thresholds("BAT0", 80, 75, True))

    def test_asus_has_only_the_end_threshold(self):
        asus(self.root)
        self.assertEqual(find_battery(self.root), Thresholds("BAT1", 80, None, False))

    def test_battery_without_thresholds_is_not_adjustable(self):
        no_thresholds(self.root)
        self.assertIsNone(find_battery(self.root))

    def test_desktop_has_nothing(self):
        self.assertIsNone(find_battery(self.root))

    def test_peripherals_and_odd_names_are_skipped(self):
        supply(self.root, "hidpp_battery_0", type="Battery", scope="Device",
               charge_control_end_threshold=80)
        supply(self.root, "odd-name", type="Battery", charge_control_end_threshold=80)
        self.assertIsNone(find_battery(self.root))
        supply(self.root, "BAT1", type="Battery", charge_control_end_threshold=90)
        self.assertEqual(find_battery(self.root).supply, "BAT1")

    def test_unreadable_values_are_none(self):
        thinkpad(self.root, charge_control_end_threshold="abc", charge_control_start_threshold=101)
        self.assertEqual(find_battery(self.root), Thresholds("BAT0", None, None, True))

    def test_read_thresholds_of_a_named_battery(self):
        thinkpad(self.root, charge_control_end_threshold=60, charge_control_start_threshold=55)
        self.assertEqual(read_thresholds(self.root, "BAT0"), Thresholds("BAT0", 60, 55, True))
        self.assertIsNone(read_thresholds(self.root, "BAT9"))
        self.assertIsNone(read_thresholds(self.root, "../BAT0"))


class Saved(Base):
    def write(self, value, mode=0o644):
        path = self.root/"charge-limit.json"
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        path.chmod(mode)
        return path

    def test_reads_the_persisted_value(self):
        path = self.write({"format": 1, "supply": "BAT0", "end": 80, "start": None})
        self.assertEqual(read_saved(path), SavedLimit("BAT0", 80, None))
        path = self.write({"format": 1, "supply": "BAT0", "end": 85, "start": 75})
        self.assertEqual(read_saved(path), SavedLimit("BAT0", 85, 75))

    def test_missing_or_invalid_state_is_none(self):
        self.assertIsNone(read_saved(self.root/"missing.json"))
        self.assertIsNone(read_saved(None))
        good = {"format": 1, "supply": "BAT0", "end": 80, "start": None}
        for value in ("garbage", "[]", dict(good, format=2), dict(good, end=49),
                      dict(good, end=True), dict(good, start=80), dict(good, supply="BAT 0"),
                      dict(good, extra=1), " " * 5000):
            with self.subTest(value=str(value)[:40]):
                self.assertIsNone(read_saved(self.write(value)))

    def test_symlink_is_not_followed(self):
        target = self.write({"format": 1, "supply": "BAT0", "end": 80, "start": None})
        link = self.root/"link.json"
        link.symlink_to(target)
        self.assertIsNone(read_saved(link))

    def test_fifo_does_not_block(self):
        path = self.root/"fifo.json"
        os.mkfifo(path)
        self.assertIsNone(read_saved(path))


if __name__ == "__main__":
    unittest.main()
