import json
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch, Mock
from gi.repository import GLib
import collectors
from collectors import Collectors
from model import CpuCounter, Counters, battery, normalize_usb, number, parse_apc

class Calculations(unittest.TestCase):
    def test_cpu_guest_not_counted_twice_and_resume_resets(self):
        cpu = CpuCounter()
        self.assertIsNone(cpu.sample([10, 0, 0, 90, 0, 0, 0, 0, 10], 1))
        self.assertAlmostEqual(cpu.sample([20, 0, 0, 100, 0, 0, 0, 0, 20], 3), 50)
        self.assertIsNone(cpu.sample([30, 0, 0, 110, 0, 0, 0, 0], 100))
    def test_rate_first_reset_resume(self):
        c = Counters()
        self.assertIsNone(c.rate("net", 100, 1))
        self.assertEqual(c.rate("net", 300, 3), 100)
        self.assertIsNone(c.rate("net", 20, 5))
        self.assertIsNone(c.rate("net", 2000, 100))
    def test_battery_levels_are_not_fake_percentages(self):
        self.assertEqual(battery(6, 55), ("電量等級：正常", None))
        self.assertEqual(battery(1, 0), ("0%", 0))
        self.assertEqual(battery(1, None), ("電量未回報", None))
        self.assertEqual(battery(1, 80, False), ("電量未回報", None))
    def test_local_keyboard_identity_is_specific(self):
        name, status, power = normalize_usb("05ac","024f","Smart","Smart Wireless Device")
        self.assertEqual(name, "Smart Wireless Device")
        name, _, _ = normalize_usb("05ac","024f","Apple","Apple Keyboard")
        self.assertEqual(name, "Apple Keyboard")
    def test_unavailable_gpu_and_apc_parsing(self):
        self.assertIsNone(number("[N/A]"))
        self.assertIsNone(number("nan"))
        self.assertEqual(number("17.0 Percent"), 17)
        self.assertEqual(parse_apc("STATUS : ONLINE\nBCHARGE : 17.0 Percent")["STATUS"], "ONLINE")

class Devices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.usb = self.root/"sys/bus/usb/devices/1-1"
        self.usb.mkdir(parents=True)
        for name, value in dict(idVendor="051d", idProduct="0002", product="APC UPS",
                                manufacturer="APC", bDeviceClass="00").items():
            (self.usb/name).write_text(value)
        self.collector = Collectors.__new__(Collectors)
        self.collector.bus, self.collector.updates = None, {}
        def mapped(path):
            path = str(path)
            return self.root/path.lstrip("/") if path.startswith("/sys/") else Path(path)
        self.mapping = patch("collectors.Path", side_effect=mapped)
        self.mapping.start()
        self.addCleanup(self.mapping.stop)

    def test_usb_upower_deduplicated(self):
        def bus(_bus, dest, path, iface, method, args=None):
            if method == "GetManagedObjects": return ({},)
            if method == "EnumerateDevices": return (["/ups"],)
            return (dict(NativePath="/sys/bus/usb/devices/1-1/hiddev0",
                         Type=3, UpdateTime=123, BatteryLevel=1, Percentage=17,
                         IsPresent=True, State=1, Model="APC UPS"),)
        with patch("collectors.dbus", side_effect=bus):
            result = self.collector.devices()
        self.assertEqual(len(result["devices"]), 1)
        self.assertEqual(result["devices"][0]["kind"], "UPS")

    def test_logitech_numeric_battery_merges_into_existing_device(self):
        source = self.collector.logitech_battery = Mock()
        source.sample.return_value = dict(percent=50, next=30, stamp=456)
        def bus(_bus, dest, path, iface, method, args=None):
            if method == "GetManagedObjects": return ({},)
            if method == "EnumerateDevices": return (["/mouse"],)
            return (dict(NativePath="/sys/bus/usb/devices/1-1/hiddev0",
                         Type=5, UpdateTime=123, BatteryLevel=6, Percentage=55,
                         IsPresent=True, State=2, Model="Example mouse"),)
        with patch("collectors.dbus", side_effect=bus):
            devices = self.collector.devices()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["percent"], 50)
        self.assertEqual(devices[0]["source"], "Solaar / HID++")
        source.observe.assert_called_once()

    def test_bluetooth_disconnected_cached_battery_hidden(self):
        def bus(_bus, dest, path, iface, method, args=None):
            if method == "GetManagedObjects":
                return ({"/org/bluez/hci0/dev_test": {"org.bluez.Device1": {
                    "Connected": False, "Alias": "Speaker"}}},)
            if method == "EnumerateDevices": return (["/battery"],)
            return (dict(NativePath="/org/bluez/hci0/dev_test", Percentage=80,
                         UpdateTime=123, IsPresent=True, BatteryLevel=1),)
        with patch("collectors.dbus", side_effect=bus):
            result = self.collector.devices()
        self.assertFalse(any(d["kind"] == "藍牙" for d in result["devices"]))

    def test_usb_survives_bluetooth_service_failure(self):
        with patch("collectors.dbus", side_effect=GLib.Error("service unavailable")):
            result = self.collector.devices()
        self.assertEqual(len(result["devices"]), 1)
        self.assertTrue(result["errors"])

    def test_gpu_timeout_preserves_other_sources(self):
        self.collector.disk_meta = []
        self.collector.last_disk_meta = time.monotonic()
        def command(args, timeout=3):
            if "nvidia-smi" in args[0]:
                raise subprocess.TimeoutExpired(args, timeout)
            return "STATUS : ONLINE\nBCHARGE : 30 Percent\n"
        with patch("collectors.command", side_effect=command):
            result = self.collector.slow()
        self.assertIsNone(result["gpu"])
        self.assertTrue(result["gpu_error"])
        self.assertEqual(result["ups"]["STATUS"], "ONLINE")

    def test_usb_remove_disappears(self):
        def bus(_bus, dest, path, iface, method, args=None):
            return ({},) if method == "GetManagedObjects" else ([],)
        with patch("collectors.dbus", side_effect=bus):
            self.assertEqual(len(self.collector.devices()["devices"]), 1)
            (self.usb/"idVendor").unlink()
            self.assertEqual(len(self.collector.devices()["devices"]), 0)

if __name__ == "__main__":
    unittest.main()
