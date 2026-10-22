"""計數器、電量、頂端列選單與裝置收集的回歸測試。"""
import json
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
from sdm.data.gpu import GpuReading
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import PowerService
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

class FakeStream:
    """取代 NvidiaStream：回傳固定讀值，不啟動任何程序。"""
    def __init__(self, readings=(), pending=False):
        self.values, self.waiting = tuple(readings), pending
    def readings(self, now=None): return self.values
    def latest(self): return self.values
    def pending(self, now=None): return self.waiting

class FakeHistory:
    def __init__(self): self.added = []
    def add(self, sample): self.added.append(sample)
    def points(self, range_s, now): return ("points", range_s, now)

class FakePage:
    def __init__(self): self.changes = 0
    def history_changed(self): self.changes += 1

class HistoryRecording(unittest.TestCase):
    """每分鐘記錄最新快照；同一快照不重複記，結束中不再記錄。"""
    def setUp(self):
        self.monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        self.monitor.closing, self.monitor.last_recorded = False, None
        self.monitor.history = FakeHistory()
        self.monitor.power_page = FakePage()

    def snapshot(self, stamp, load_w=288.0):
        ups = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_w=load_w)
        service = PowerService(read_ups=lambda: ups, read_gpus=lambda: (),
                               wall=lambda: stamp, read_uptime=lambda: None)
        return service.sample()

    def record(self, power):
        self.monitor.data = {"slow": {"power": power}}
        return self.monitor.record_history()

    def test_records_each_new_snapshot_once(self):
        self.assertTrue(self.record(self.snapshot(1000.0)))
        self.assertTrue(self.record(self.snapshot(1000.0)))
        self.assertTrue(self.record(self.snapshot(1060.0, 250.0)))
        self.assertEqual([(s.t, s.watts) for s in self.monitor.history.added],
                         [(1000.0, 288.0), (1060.0, 250.0)])
        self.assertEqual(self.monitor.power_page.changes, 2)

    def test_chart_points_come_from_the_history(self):
        self.assertEqual(self.monitor.history_points(3600, 5.0), ("points", 3600, 5.0))
        self.monitor.history = None
        self.assertEqual(self.monitor.history_points(3600, 5.0), ())

    def test_nothing_without_a_snapshot_and_stops_when_closing(self):
        self.assertTrue(self.record(None))
        self.assertEqual(self.monitor.history.added, [])
        self.monitor.closing = True
        self.assertFalse(self.record(self.snapshot(1000.0)))
        self.assertEqual(self.monitor.history.added, [])

class FakeStack:
    def __init__(self): self.visible = None
    def set_visible_child_name(self, name): self.visible = name
    def get_visible_child_name(self): return self.visible

class FakeScroll:
    def __init__(self, mapped=True): self.mapped = mapped
    def get_mapped(self): return self.mapped

class SmokePages(unittest.TestCase):
    """模擬測試依序顯示各頁，只有真的切換並顯示出來的頁面才列入 pages。"""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        self.monitor.output_dir = Path(temp.name)
        self.monitor.capture_enabled = False
        self.monitor.stack = FakeStack()
        self.monitor.scrolls = {name: FakeScroll() for name in ui_app.SMOKE_PAGES}
        self.monitor.smoke_pages = []
        self.monitor.errors, self.monitor.indicators = {}, {}
        self.monitor.data = {"system": {"cpu": 1}}
        self.stopped = []
        self.monitor.stop = lambda: self.stopped.append(True)

    def test_power_page_comes_first(self):
        self.assertEqual(ui_app.SMOKE_PAGES, ("power", "overview", "devices", "settings"))

    def test_rendered_pages_are_reported(self):
        with patch.object(ui_app.GLib, "timeout_add") as timer:
            for index, name in enumerate(ui_app.SMOKE_PAGES):
                self.monitor.stack.set_visible_child_name(name)
                self.monitor.smoke_capture(index)
            self.assertEqual(timer.call_count, len(ui_app.SMOKE_PAGES))
        self.monitor.scrolls["devices"].mapped = False
        self.monitor.smoke_finish()
        report = json.loads((self.monitor.output_dir/"smoke.json").read_text())
        self.assertEqual(report["pages"], list(ui_app.SMOKE_PAGES))
        self.assertTrue(report["ok"])
        self.assertEqual(self.stopped, [True])

    def test_page_that_was_not_shown_is_left_out(self):
        with patch.object(ui_app.GLib, "timeout_add"):
            self.monitor.stack.set_visible_child_name("overview")
            self.monitor.smoke_capture(0)
            self.monitor.scrolls["overview"].mapped = False
            self.monitor.smoke_capture(1)
        self.assertEqual(self.monitor.smoke_pages, [])

class SmokeHistory(unittest.TestCase):
    def test_smoke_test_uses_an_in_memory_history(self):
        self.assertIsNone(ui_app.create_history(smoke=True).path)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(ui_app.paths, "DATA", Path(temp)):
                self.assertEqual(ui_app.create_history(smoke=False).path,
                                 Path(temp)/"history.json")

class Signals(unittest.TestCase):
    """登出或 kill 送來的 SIGTERM／SIGINT 走正常結束流程（存歷史、停止 nvidia-smi）。"""
    def test_signal_runs_the_normal_stop_and_removes_its_source(self):
        monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        stopped = []
        monitor.stop = lambda: stopped.append(True)
        self.assertEqual(monitor.on_signal(), ui_app.GLib.SOURCE_REMOVE)
        self.assertEqual(stopped, [True])

    def test_signal_sources_are_kept_out_of_the_timer_list(self):
        monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        monitor.timers, monitor.signal_sources = [], []
        with patch.object(ui_app.GLib, "unix_signal_add", return_value=9) as add:
            monitor.install_signal_handlers()
        self.assertEqual(sorted(call.args[1] for call in add.call_args_list),
                         sorted([ui_app.signal.SIGTERM, ui_app.signal.SIGINT]))
        self.assertEqual(monitor.timers, [])
        self.assertEqual(monitor.signal_sources, [9, 9])

class Recorder:
    """記錄呼叫順序；raises 中的步驟會拋出例外。"""
    def __init__(self, raises=()):
        self.calls, self.raises = [], set(raises)
    def step(self, name):
        def action(*args, **kwargs):
            self.calls.append(name)
            if name in self.raises:
                raise RuntimeError(name)
        return action

class FakeBus:
    def __init__(self, recorder): self.signal_unsubscribe = recorder.step("unsubscribe")

class FakeCollector:
    def __init__(self, recorder):
        self.bus = FakeBus(recorder)
        self.nvidia = type("Stream", (), {"stop": staticmethod(recorder.step("stream"))})()

class StatusIndicator:
    def __init__(self, recorder): self.set_status = recorder.step("indicator")

class StopOrder(unittest.TestCase):
    """結束時先存歷史，任何一步失敗都不能讓 quit() 被跳過。"""
    def monitor(self, raises=()):
        recorder = Recorder(raises)
        monitor = ui_app.Monitor.__new__(ui_app.Monitor)
        monitor.closing, monitor.timers, monitor.event_timer = False, [], 0
        monitor.subscriptions = [1]
        monitor.collector = FakeCollector(recorder)
        monitor.history = type("History", (), {"save": staticmethod(recorder.step("save"))})()
        monitor.indicator = StatusIndicator(recorder)
        monitor.indicators = {}
        monitor.pool = type("Pool", (), {"shutdown": staticmethod(recorder.step("pool"))})()
        monitor.quit = recorder.step("quit")
        return monitor, recorder

    def test_history_is_saved_before_the_stream_stops(self):
        monitor, recorder = self.monitor()
        monitor.stop()
        self.assertEqual(recorder.calls[0], "save")
        self.assertLess(recorder.calls.index("save"), recorder.calls.index("stream"))
        self.assertEqual(recorder.calls[-1], "quit")

    def test_failures_do_not_skip_later_steps_or_quit(self):
        monitor, recorder = self.monitor(raises=("save", "unsubscribe", "stream"))
        with self.assertLogs(level="WARNING") as logs:
            monitor.stop()
        self.assertEqual(recorder.calls[-1], "quit")
        self.assertIn("stream", recorder.calls)
        self.assertTrue(all("RuntimeError" in line for line in logs.output))

    def test_second_stop_does_nothing(self):
        monitor, recorder = self.monitor()
        monitor.stop()
        count = len(recorder.calls)
        monitor.stop()
        self.assertEqual(len(recorder.calls), count)

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

    def test_laptop_system_battery_is_not_a_peripheral(self):
        def bus(_bus, dest, path, iface, method, args=None):
            if method == "GetManagedObjects": return ({},)
            if method == "EnumerateDevices": return (["/battery_BAT0", "/mouse"],)
            if path == "/battery_BAT0":
                return (dict(NativePath="BAT0", Type=2, PowerSupply=True, UpdateTime=123,
                             BatteryLevel=1, Percentage=82, IsPresent=True, State=2,
                             Model="Example Cells"),)
            return (dict(NativePath="hidpp_battery_0", Type=5, PowerSupply=False,
                         UpdateTime=123, BatteryLevel=1, Percentage=55, IsPresent=True,
                         State=2, Model="Example Mouse"),)
        with patch("sdm.data.collectors.dbus", side_effect=bus):
            devices = self.collector.devices()["devices"]
        self.assertEqual([d["name"] for d in devices if d["kind"] != "USB"], ["Example Mouse"])

    def test_peripheral_battery_of_type_battery_is_still_listed(self):
        def bus(_bus, dest, path, iface, method, args=None):
            if method == "GetManagedObjects": return ({},)
            if method == "EnumerateDevices": return (["/battery_hidpp_battery_0"],)
            return (dict(NativePath="hidpp_battery_0", Type=2, PowerSupply=False,
                         UpdateTime=123, BatteryLevel=1, Percentage=55, IsPresent=True,
                         State=2, Model="Example Pad"),)
        with patch("sdm.data.collectors.dbus", side_effect=bus):
            devices = self.collector.devices()["devices"]
        listed = [d for d in devices if d["kind"] != "USB"]
        self.assertEqual([(d["name"], d["kind"], d["percent"]) for d in listed],
                         [("Example Pad", "無線周邊", 55)])

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

    def slow(self, gpus=(), amd=(), pending=False, ups="STATUS : ONLINE\nBCHARGE : 30 Percent\n",
             batteries=(), method="slow"):
        self.collector.disk_meta = []
        self.collector.last_disk_meta = time.monotonic()
        self.collector.nvidia = FakeStream(gpus, pending)
        self.collector.cpu_info = dict(name="Example CPU")
        self.collector.power = self.collector.make_power_service(model="Example Board")
        def command(args, timeout=3):
            self.assertNotIn("nvidia-smi", args[0])  # 改由常駐串流提供，不再每次啟動
            if isinstance(ups, Exception):
                raise ups
            return ups
        # 測試不讀這台電腦真正的電池：預設沒有系統電池（桌機）
        with patch("sdm.data.collectors.command", side_effect=command), \
                patch("sdm.data.collectors.read_amd_gpus", return_value=amd), \
                patch("sdm.data.collectors.read_batteries", return_value=batteries), \
                patch("sdm.data.collectors.read_adapters", return_value=()):
            return getattr(self.collector, method)()

    def test_laptop_battery_gives_a_laptop_snapshot(self):
        battery = BatteryReading(name="BAT0", status="Discharging", capacity=82, power_w=15.0)
        result = self.slow(batteries=(battery,))
        power = result["power"]
        self.assertIsInstance(power, LaptopPower)
        self.assertEqual((power.percent, power.system_w), (82, 15.0))
        self.assertTrue(result["ups"].online)
        self.assertIs(result["ups"], power.ups)

    def test_power_refresh_samples_only_the_power_snapshot(self):
        battery = BatteryReading(name="BAT0", status="Discharging", capacity=82, power_w=15.0)
        result = self.slow(batteries=(battery,), method="power_refresh")
        self.assertEqual(sorted(result), ["power", "stamp"])
        self.assertIsInstance(result["power"], LaptopPower)

    def test_missing_gpu_preserves_ups_reading(self):
        result = self.slow()
        self.assertIsNone(result["gpu"])
        self.assertTrue(result["gpu_error"])
        self.assertTrue(result["ups"].online)
        self.assertEqual(result["ups"].charge_pct, 30.0)

    def test_gpu_card_prefers_nvidia_then_amd_discrete(self):
        now = time.monotonic()
        nvidia = GpuReading(vendor="nvidia", index=0, name="Example A", watts=61.5, stamp=now)
        apu = GpuReading(vendor="amd", index=0, name="AMD", integrated=True, stamp=now)
        dgpu = GpuReading(vendor="amd", index=1, name="AMD", watts=150.0, stamp=now)
        result = self.slow(gpus=(nvidia,), amd=(apu, dgpu))
        self.assertIs(result["gpu"], nvidia)
        self.assertEqual(result["gpus"], (nvidia, apu, dgpu))
        self.assertIs(self.slow(amd=(apu, dgpu))["gpu"], dgpu)

    def test_waiting_for_first_stream_row_is_not_an_error(self):
        result = self.slow(pending=True)
        self.assertIsNone(result["gpu"])
        self.assertEqual(result["gpu_error"], "")

    def test_power_snapshot_uses_ups_load_and_gpu_power(self):
        nvidia = GpuReading(vendor="nvidia", index=0, name="Example A", watts=61.5,
                            stamp=time.monotonic())
        result = self.slow(gpus=(nvidia,), ups="STATUS : ONLINE\nLOADPCT : 32.0 Percent\n"
                                                 "NOMPOWER : 900 Watts\n")
        power = result["power"]
        self.assertEqual((power.system_w, power.breakdown.other_w), (288.0, 226.5))
        self.assertEqual((power.processor, power.model), ("Example CPU", "Example Board"))
        self.assertIs(result["ups"], power.ups)
        self.assertEqual(result["gpus"], power.gpus)

    def test_ups_failure_gives_no_reading(self):
        result = self.slow(ups=subprocess.TimeoutExpired("apcaccess", 2))
        self.assertIsNone(result["ups"])
        comm = self.slow(ups="STATUS : COMMLOST\n")["ups"]
        self.assertTrue(comm.comm_lost)

    def test_usb_remove_disappears(self):
        def bus(_bus, dest, path, iface, method, args=None):
            return ({},) if method == "GetManagedObjects" else ([],)
        with patch("sdm.data.collectors.dbus", side_effect=bus):
            self.assertEqual(len(self.collector.devices()["devices"]), 1)
            (self.usb/"idVendor").unlink()
            self.assertEqual(len(self.collector.devices()["devices"]), 0)

if __name__ == "__main__":
    unittest.main()
