"""人工建立的電量測試資料：不含實機擷取的裝置識別碼或原始日誌。"""
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch
from sdm.data.logitech_battery import SolaarBattery, apply_battery, parse_solaar_show

REPORT = """Lightspeed Receiver
  Device path  : /dev/hidraw1
  USB id       : 046d:C53F
  1: G305 Lightspeed Wireless Gaming Mouse
     Device path  : /dev/hidraw2
     WPID         : 4074
     Protocol     : HID++ 4.2
     Serial number: PRIVATE_TEST_VALUE
         5: BATTERY STATUS         {1000} V0
            Battery: 90%, discharging, next level 50%.
     Battery: 50%, discharging, next level 30%.
"""
PARENT = "/sys/devices/usb1/1-1"
IDENTITY = ("/dev/hidraw2", "/sys/devices/usb1/1-1/hid/device1", "2", True)


class Parsing(unittest.TestCase):
    def test_final_report_wins_and_identifiers_are_discarded(self):
        self.assertEqual(parse_solaar_show(REPORT),
                         {"/dev/hidraw2": {"percent": 50, "next": 30}})

    def test_unknown_offline_unsupported_and_zero_are_not_percentages(self):
        for text in (
            REPORT.replace("50%, discharging, next level 30%.", "unknown (device is offline)."),
            REPORT.replace("HID++ 4.2", "unknown (device is offline)"),
            REPORT.replace("4074", "1234"),
            REPORT.replace("BATTERY STATUS", "OTHER FEATURE"),
            REPORT.replace("50%, discharging, next level 30%.", "0%, discharging, next level 30%."),
            REPORT.replace("50%, discharging, next level 30%.", "101%, discharging, next level 30%."),
            REPORT + "     Battery status unavailable.\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_solaar_show(text), {})

    def test_multiple_devices_use_distinct_hid_paths(self):
        second = REPORT.replace("/dev/hidraw2", "/dev/hidraw3").replace(
            "50%, discharging, next level 30%.", "90%, discharging, next level 50%.")
        self.assertEqual(len(parse_solaar_show(REPORT + second)), 2)

    def test_later_unrecognized_section_cannot_overwrite_previous_device(self):
        extra = """Another Receiver
     Device path  : /dev/hidraw9
     Protocol     : HID++ 4.2
     Battery: 90%, discharging, next level 50%.
"""
        self.assertEqual(parse_solaar_show(REPORT + extra),
                         {"/dev/hidraw2": {"percent": 50, "next": 30}})


class Sampling(unittest.TestCase):
    def setUp(self):
        self.source = SolaarBattery()
        self.current = {PARENT: IDENTITY}
        self.inv = patch("sdm.data.logitech_battery.inventory", side_effect=lambda: self.current.copy())
        self.inv.start()
        self.addCleanup(self.inv.stop)

    def refresh(self):
        with patch("sdm.data.logitech_battery.subprocess.run", return_value=SimpleNamespace(stdout=REPORT)) as run:
            self.assertTrue(self.source.refresh()["available"])
            self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_merge_replaces_coarse_upower_value_and_preserves_identity(self):
        self.refresh()
        item = dict(id="existing", battery="電量等級：正常", percent=None)
        apply_battery(item, self.source.sample(PARENT))
        self.assertEqual(item["id"], "existing")
        self.assertEqual(item["percent"], 50)
        self.assertEqual(item["battery"], "約 50%（分段回報）")

    def test_expired_or_suspended_sample_is_unavailable(self):
        self.refresh()
        sample = self.source.sample(PARENT)
        with patch("sdm.data.logitech_battery.time.monotonic", return_value=sample["monotonic"] + 91):
            self.assertIsNone(self.source.sample(PARENT))
        with patch("sdm.data.logitech_battery.time.time", return_value=sample["stamp"] + 91):
            self.assertIsNone(self.source.sample(PARENT))

    def test_offline_then_online_does_not_restore_old_value(self):
        self.refresh()
        self.current[PARENT] = (*IDENTITY[:-1], False)
        self.source.observe()
        self.assertIsNone(self.source.sample(PARENT))
        self.current[PARENT] = IDENTITY
        self.source.observe()
        self.assertIsNone(self.source.sample(PARENT))

    def test_receiver_unplug_and_replug_invalidates_cached_battery(self):
        self.refresh()
        self.current.clear()
        self.source.observe()
        self.assertIsNone(self.source.sample(PARENT))
        self.current[PARENT] = (*IDENTITY[:2], "3", True)
        self.source.observe()
        self.assertIsNone(self.source.sample(PARENT))

    def test_failure_discards_previous_numeric_sample(self):
        for failure in (FileNotFoundError(), PermissionError(),
                        subprocess.TimeoutExpired("solaar", 15),
                        subprocess.CalledProcessError(1, "solaar")):
            with self.subTest(failure=type(failure).__name__):
                self.refresh()
                with patch("sdm.data.logitech_battery.subprocess.run", side_effect=failure):
                    self.assertFalse(self.source.refresh()["available"])
                self.assertIsNone(self.source.sample(PARENT))

    def test_offline_change_during_query_rejects_late_result(self):
        def run(*args, **kwargs):
            self.current[PARENT] = (*IDENTITY[:-1], False)
            self.source.observe()
            self.current[PARENT] = IDENTITY
            self.source.observe()
            return SimpleNamespace(stdout=REPORT)
        with patch("sdm.data.logitech_battery.subprocess.run", side_effect=run):
            self.assertFalse(self.source.refresh()["available"])
        self.assertIsNone(self.source.sample(PARENT))

    def test_no_active_device_skips_external_process(self):
        self.current.clear()
        with patch("sdm.data.logitech_battery.subprocess.run") as run:
            self.assertFalse(self.source.refresh()["available"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
