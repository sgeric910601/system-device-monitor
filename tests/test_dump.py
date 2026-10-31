"""--dump 自我檢查：輸出格式固定，且不含序號、MAC、IP、主機名稱或家目錄／掛載路徑。"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gi.repository import GLib

from sdm.data import upower
from sdm.paths import VERSION
from sdm.services import dump
from tests.fixtures import sysfs

ROOT = Path(__file__).resolve().parents[1]
MAC_PATTERN = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}(?![0-9a-f])")
IP_PATTERN = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
PRIVATE_DIRS = ("/" + "home" + "/", "/" + "mnt" + "/", "/" + "Users" + "/")
TOP_KEYS = {"app_version", "mode", "chassis_type", "power_supply", "charge_thresholds",
            "adapters", "ups_present", "gpus", "upower_available", "power_profiles_available"}
ENTRY_KEYS = {"name", "type", "scope", "status", "system_battery", "adapter", "attributes"}


def fake_mac():
    return ":".join(["0a"] * 6)


def unavailable(*_):
    raise GLib.Error("unavailable")


def present(destination, path, interface, name):
    return {"OnBattery": False, "Type": 2, "IsPresent": True, "ActiveProfile": "balanced"}.get(
        name, 0)


def runner(outputs):
    def run(args, timeout=None):
        output = outputs.get(Path(args[0]).name)
        if isinstance(output, Exception):
            raise output
        if output is None:
            raise FileNotFoundError(args[0])
        return output
    return run


class Dump(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def build(self, get=unavailable, outputs=None):
        return dump.build_dump(self.root, get, runner(outputs or {}))

    def assert_private_data_absent(self, report):
        text = json.dumps(report, ensure_ascii=False)
        self.assertIsNone(MAC_PATTERN.search(text))
        self.assertIsNone(IP_PATTERN.search(text))
        for directory in PRIVATE_DIRS + (str(self.root), str(ROOT)):
            self.assertNotIn(directory, text)
        self.assertNotIn(sysfs.FAKE_SERIAL, text)
        hostname = socket.gethostname()
        if len(hostname) >= 4:
            self.assertNotIn(hostname, text)
        return text

    def test_laptop_schema(self):
        sysfs.thinkpad(self.root, online=1)
        sysfs.peripheral(self.root, name="hid-" + fake_mac() + "-battery")
        (self.root/"sys/class/dmi/id").mkdir(parents=True)
        (self.root/"sys/class/dmi/id/chassis_type").write_text("10\n")
        report = self.build(present, {"apcaccess": "STATUS   : ONLINE\n",
                                      "nvidia-smi": "Example GPU\n"})
        self.assertEqual(set(report), TOP_KEYS)
        self.assertEqual((report["app_version"], report["mode"], report["chassis_type"]),
                         (VERSION, "laptop", "10"))
        self.assertTrue(report["charge_thresholds"])
        self.assertEqual(report["adapters"], [{"type": "Mains", "online": True}])
        self.assertEqual((report["ups_present"], report["upower_available"],
                          report["power_profiles_available"]), (True, True, True))
        self.assertEqual(report["gpus"], [{"vendor": "nvidia", "name": "Example GPU"}])
        entries = {entry["name"]: entry for entry in report["power_supply"]}
        self.assertEqual(sorted(entries), ["AC", "BAT0", "hid-xx:xx:xx:xx:xx:xx-battery"])
        self.assertTrue(all(set(entry) == ENTRY_KEYS for entry in entries.values()))
        battery = entries["BAT0"]
        self.assertEqual((battery["type"], battery["scope"], battery["status"],
                          battery["system_battery"]), ("Battery", "", "Discharging", True))
        self.assertIn("charge_control_end_threshold", battery["attributes"])
        self.assertNotIn("serial_number", battery["attributes"])
        self.assertFalse(entries["hid-xx:xx:xx:xx:xx:xx-battery"]["system_battery"])
        text = self.assert_private_data_absent(report)
        self.assertNotIn("41000000", text)          # 不輸出讀值

    def test_desktop_with_nothing_available(self):
        sysfs.peripheral(self.root)
        report = self.build(outputs={"apcaccess": OSError("missing"),
                                     "nvidia-smi": subprocess.TimeoutExpired("nvidia-smi", 3)})
        self.assertEqual((report["mode"], report["charge_thresholds"], report["chassis_type"]),
                         ("desktop", False, None))
        self.assertEqual((report["ups_present"], report["gpus"], report["upower_available"],
                          report["power_profiles_available"], report["adapters"]),
                         (False, [], False, False, []))
        self.assert_private_data_absent(report)

    def test_uevent_and_unreadable_files_are_not_listed(self):
        directory = sysfs.supply(self.root, "BAT0", type="Battery", uevent="X", capacity=50)
        (directory/"device_uuid").write_text("x\n")
        (directory/"subdir").mkdir()
        names = dump.attribute_names(directory)
        self.assertEqual(names, ["capacity", "type"])

    def test_names_are_sanitized(self):
        self.assertEqual(dump.safe_name("sony_controller_battery_" + "0a_" * 5 + "0a"),
                         "sony_controller_battery_xx:xx:xx:xx:xx:xx")
        self.assertEqual(dump.safe_name("BAT0\x07"), "BAT0")


class EntryPoint(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("device_monitor_entry",
                                                      ROOT/"device_monitor.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_dump_prints_one_json_object(self):
        output = io.StringIO()
        with patch.object(dump, "build_dump", return_value={"mode": "desktop"}) as build, \
                patch.object(dump, "system_getter", return_value=unavailable), \
                contextlib.redirect_stdout(output):
            code = self.load().main(["device_monitor.py", "--dump"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"mode": "desktop"})
        build.assert_called_once()

    def test_preview_choices(self):
        parser = self.load().build_parser()
        options = parser.parse_args(["--preview", "laptop-limit", "--smoke-test"])
        self.assertEqual(options.preview, "laptop-limit")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--preview", "tablet"])

    def test_abbreviated_options_are_rejected(self):
        """argparse 預設接受縮寫（--dum）；啟動器只認得完整的選項，所以縮寫一律當成錯誤。"""
        parser = self.load().build_parser()
        for argv in (["--dum"], ["--vers"], ["--back"], ["--charging-plan"], ["--test-notif"],
                     ["--prev", "desktop"], ["--smoke"]):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        self.assertRaises(SystemExit) as stop:
                    parser.parse_args(argv)
                self.assertEqual(stop.exception.code, 2)     # 參數錯誤，不是執行了 --version
        self.assertTrue(parser.parse_args(["--dump"]).dump)

    def test_preview_rejects_options_it_cannot_honour(self):
        module = self.load()
        for argv in (["--preview", "desktop", "--background"],
                     ["--preview", "desktop", "--smoke-test", "--capture"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                module.main(["device_monitor.py"] + argv)

    def test_upower_names_are_not_private(self):
        self.assertTrue(upower.DESTINATION.startswith("org.freedesktop."))


if __name__ == "__main__":
    unittest.main()
