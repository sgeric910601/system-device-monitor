"""計數器、電量、頂端列選單與裝置收集的回歸測試。"""
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch, Mock
from gi.repository import GLib
from sdm.core.counters import CpuCounter, Counters
from sdm.core.devices import battery, battery_menu_rows, normalize_usb, parse_apc
from sdm.core.fmt import number
from sdm.data.collectors import Collectors
from sdm.ui import app as ui_app

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

def peripheral(key, kind, text, value):
    return dict(id=key, name="Example " + key, kind=kind, battery=text, percent=value)

class BatteryMenuRows(unittest.TestCase):
    def test_only_peripherals_that_report_a_charge_are_listed(self):
        devices = [peripheral("ups", "UPS", "100%", 100.0),
                   peripheral("level", "無線周邊", "電量等級：正常", None),
                   peripheral("coarse", "無線周邊", "約 30%（分段回報）", 30),
                   peripheral("keyboard", "藍牙", "53%", 53),
                   peripheral("speaker", "藍牙", "電量未回報", None),
                   peripheral("receiver", "USB", "有電池 · 電量未回報", None),
                   peripheral("adapter", "USB", "電量不適用／未回報", None)]
        self.assertEqual(battery_menu_rows(dict(devices=devices, errors=[], stamp=1)), [
            ("level", "Example level · 電量等級：正常"),
            ("coarse", "Example coarse · 約 30%（分段回報）"),
            ("keyboard", "Example keyboard · 53%")])

    def test_placeholder_before_first_result(self):
        self.assertEqual(battery_menu_rows({"devices": [], "errors": []}), [(None, "讀取中…")])

    def test_collector_failure_is_not_shown_as_loading(self):
        self.assertEqual(battery_menu_rows({"devices": [], "errors": ["設備資料暫時無法取得"]}),
                         [(None, "設備資料暫時無法取得")])

    def test_partial_failure_without_batteries_shows_errors(self):
        payload = dict(devices=[peripheral("adapter", "USB", "電量不適用／未回報", None)],
                       errors=["藍牙服務暫時無法取得", "電池資料服務暫時無法取得"], stamp=1)
        self.assertEqual(battery_menu_rows(payload),
                         [(None, "藍牙服務暫時無法取得 · 電池資料服務暫時無法取得")])

    def test_no_battery_devices(self):
        payload = dict(devices=[peripheral("ups", "UPS", "100%", 100.0)], errors=[], stamp=1)
        self.assertEqual(battery_menu_rows(payload), [(None, "目前沒有設備回報電量")])

class FakeItem:
    def __init__(self, label):
        self.label, self.sensitive, self.visible, self.destroyed = label, True, False, False
        self.handlers = []
    def get_label(self): return self.label
    def set_label(self, label): self.label = label
    def set_sensitive(self, value): self.sensitive = value
    def show(self): self.visible = True
    def connect(self, signal, callback, *args): self.handlers.append((signal, callback, args))
    def destroy(self): self.destroyed = True

class FakeMenu:
    def __init__(self, children): self.children = list(children)
    def get_children(self): return list(self.children)
    def insert(self, item, position): self.children.insert(position, item)
    def remove(self, item): self.children.remove(item)

class BatteryMenuSync(unittest.TestCase):
    """不使用真正的 GTK 元件：沒有顯示器時建立元件會讓程序中止。"""
    def setUp(self):
        self.monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        self.monitor.battery_header = FakeItem("設備電量")
        self.monitor.menu = FakeMenu([FakeItem("UPS"), self.monitor.battery_header, FakeItem("---")])
        self.monitor.battery_items, self.monitor.battery_rows = [], None
        self.monitor.battery_item = FakeItem
        self.indent = ui_app.MENU_INDENT

    def labels(self):
        return [item.get_label() for item in self.monitor.menu.get_children()]

    def test_rows_are_inserted_below_header_and_open_devices_page(self):
        self.monitor.sync_battery_items([("a", "A · 50%"), ("b", "B · 60%")])
        self.assertEqual(self.labels(), ["UPS", "設備電量", self.indent + "A · 50%",
                                         self.indent + "B · 60%", "---"])
        for item in self.monitor.battery_items:
            self.assertTrue(item.visible and item.sensitive)
            self.assertEqual(item.handlers, [("activate", self.monitor.open_page, ("devices",))])

    def test_same_devices_update_labels_in_place(self):
        self.monitor.sync_battery_items([("a", "A · 50%")])
        first = self.monitor.battery_items[0]
        self.monitor.sync_battery_items([("a", "A · 40%")])
        self.assertIs(self.monitor.battery_items[0], first)
        self.assertFalse(first.destroyed)
        self.assertEqual(self.labels(), ["UPS", "設備電量", self.indent + "A · 40%", "---"])

    def test_changed_devices_rebuild_rows(self):
        self.monitor.sync_battery_items([("a", "A · 50%"), ("b", "B · 60%")])
        old = list(self.monitor.battery_items)
        self.monitor.sync_battery_items([("b", "B · 60%"), ("c", "C · 70%")])
        self.assertTrue(all(item.destroyed for item in old))
        self.assertEqual(self.labels(), ["UPS", "設備電量", self.indent + "B · 60%",
                                         self.indent + "C · 70%", "---"])

    def test_placeholder_row_is_not_clickable(self):
        self.monitor.sync_battery_items([(None, "讀取中…")])
        item = self.monitor.battery_items[0]
        self.assertFalse(item.sensitive)
        self.assertEqual(item.handlers, [])
        self.assertEqual(self.labels(), ["UPS", "設備電量", self.indent + "讀取中…", "---"])

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
        self.mapping = patch("sdm.data.collectors.Path", side_effect=mapped)
        self.mapping.start()
        self.addCleanup(self.mapping.stop)

    def test_usb_upower_deduplicated(self):
        def bus(_bus, dest, path, iface, method, args=None):
            if method == "GetManagedObjects": return ({},)
            if method == "EnumerateDevices": return (["/ups"],)
            return (dict(NativePath="/sys/bus/usb/devices/1-1/hiddev0",
                         Type=3, UpdateTime=123, BatteryLevel=1, Percentage=17,
                         IsPresent=True, State=1, Model="APC UPS"),)
        with patch("sdm.data.collectors.dbus", side_effect=bus):
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
        with patch("sdm.data.collectors.dbus", side_effect=bus):
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
        with patch("sdm.data.collectors.dbus", side_effect=bus):
            result = self.collector.devices()
        self.assertFalse(any(d["kind"] == "藍牙" for d in result["devices"]))

    def test_usb_survives_bluetooth_service_failure(self):
        with patch("sdm.data.collectors.dbus", side_effect=GLib.Error("service unavailable")):
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
        with patch("sdm.data.collectors.command", side_effect=command):
            result = self.collector.slow()
        self.assertIsNone(result["gpu"])
        self.assertTrue(result["gpu_error"])
        self.assertEqual(result["ups"]["STATUS"], "ONLINE")

    def test_usb_remove_disappears(self):
        def bus(_bus, dest, path, iface, method, args=None):
            return ({},) if method == "GetManagedObjects" else ([],)
        with patch("sdm.data.collectors.dbus", side_effect=bus):
            self.assertEqual(len(self.collector.devices()["devices"]), 1)
            (self.usb/"idVendor").unlink()
            self.assertEqual(len(self.collector.devices()["devices"]), 0)

if __name__ == "__main__":
    unittest.main()
