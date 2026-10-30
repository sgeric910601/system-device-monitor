"""可攜安裝、隱私檢查與感測器配對的回歸測試。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sdm import paths
from sdm.core.devices import normalize_usb
from sdm.core.settings import LEGACY_DEFAULTS
from sdm.data import collectors
from sdm.services import autostart

ROOT = Path(__file__).resolve().parents[1]

class Portability(unittest.TestCase):
    def test_generic_keyboard_is_not_assumed_to_be_a_specific_model(self):
        self.assertEqual(normalize_usb("05ac", "024f", "Smart", "Smart Wireless Device")[0],
                         "Smart Wireless Device")
        aliases = {"1234:5678|Example|Receiver": dict(name="Keyboard", status="Unknown", battery="Unknown")}
        self.assertEqual(normalize_usb("1234", "5678", "Example", "Receiver", aliases)[0], "Keyboard")
        self.assertEqual(normalize_usb("1234", "5678", "Other", "Receiver", aliases)[0], "Receiver")
        self.assertEqual(normalize_usb("1234", "5678", "Example", "Receiver", {"1234:5678|Example|Receiver": 1})[0], "Receiver")

    def test_cpu_topology_handles_two_sockets_and_threads(self):
        records = []
        for socket, core in ((0,0),(0,0),(1,0),(1,0)):
            records.append(f"processor : {len(records)}\nmodel name : Example CPU\nphysical id : {socket}\ncore id : {core}")
        with patch("sdm.data.collectors.read", return_value="\n\n".join(records)):
            self.assertEqual(collectors.cpu_info(), dict(name="Example CPU", cores=2, threads=4))

    def test_cpu_temperature_is_not_taken_from_gpu(self):
        sensors = [dict(chip="amdgpu", label="edge", value=99),
                   dict(chip="coretemp", label="Package id 0", value=42)]
        self.assertEqual(collectors.cpu_temperature(sensors), (42, "Package id 0"))
        self.assertIsNone(collectors.cpu_temperature(sensors[:1])[0])

    def test_disk_sensors_match_hardware_not_enumeration(self):
        sensors = [
            dict(device="/sys/devices/controller/nvme/nvme1", label="Composite", value=50),
            dict(device="/sys/devices/controller/nvme/nvme0", label="Sensor 1", value=60),
            dict(device="/sys/devices/controller/nvme/nvme0", label="Composite", value=40),
        ]
        found = collectors.device_temperatures(sensors, "/sys/devices/controller/nvme/nvme0")
        self.assertEqual(collectors.storage_temperature(found), 40)
        self.assertEqual(collectors.device_temperatures(sensors, "/sys/devices/other/sda"), [])
        self.assertIsNone(collectors.storage_temperature([]))

    def test_network_phy_matches_only_its_parent_device(self):
        sensors = [dict(device="/sys/devices/pci/nic/mdio/phy", label="temp1", value=38)]
        self.assertEqual(len(collectors.device_temperatures(sensors, "/sys/devices/pci/nic")), 1)
        self.assertEqual(collectors.device_temperatures(sensors, "/sys/devices/pci/nic2"), [])

    def test_missing_system_bus_keeps_core_collectors_available(self):
        from gi.repository import GLib
        with patch("sdm.data.collectors.Gio.bus_get_sync", side_effect=GLib.Error("unavailable")), \
             patch("sdm.data.collectors.load_json", return_value={}):
            collector = collectors.Collectors()
        self.assertIsNone(collector.bus)
        with self.assertRaises(GLib.Error):
            collectors.dbus(None, "", "", "", "")
        self.assertIn("ram", collector.system())

    def test_first_run_and_disabled_autostart_survive_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/"settings.json"
            auto = Path(tmp)/"autostart/app.desktop"
            with patch.multiple(paths, CONFIG=config, AUTOSTART=auto):
                autostart.initialize_preferences()
                self.assertTrue(auto.exists())
                autostart.set_autostart(False)
                autostart.initialize_preferences()
                self.assertFalse(auto.exists())
                self.assertEqual(json.loads(config.read_text()), dict(LEGACY_DEFAULTS))

    def test_launcher_handles_spaces_and_shell_metacharacters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/"checkout with spaces '$dollar"
            root.mkdir()
            (root/"device_monitor.py").write_text("import sys; print(sys.argv[1])")
            launcher = Path(tmp)/"launcher"
            launcher.write_text(autostart.launcher_script(root))
            launcher.chmod(0o755)
            # 啟動器會建立快取目錄；指向暫存目錄，避免碰到真實的使用者快取。
            env = {**os.environ, "XDG_CACHE_HOME": str(Path(tmp)/"cache")}
            result = subprocess.check_output([str(launcher), "--version"], text=True, env=env)
            self.assertEqual(result.strip(), "--version")

    def test_desktop_exec_handles_spaces_and_metacharacters(self):
        from gi.repository import Gio
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'launcher with spaces $dollar %field "quote'
            output = Path(tmp)/"result"
            target.write_text("#!/usr/bin/python3\nfrom pathlib import Path\nPath("+repr(str(output))+").write_text('ok')\n")
            target.chmod(0o755)
            entry = Path(tmp)/"monitor.desktop"
            entry.write_text(autostart.desktop_entry(launcher=target))
            subprocess.run(["desktop-file-validate", str(entry)], check=True)
            app = Gio.DesktopAppInfo.new_from_filename(str(entry))
            self.assertIsNotNone(app)
            app.launch([], None)
            import time
            for _ in range(50):
                if output.exists(): break
                time.sleep(.02)
            self.assertEqual(output.read_text(), "ok")

    def test_package_root_uses_system_launcher(self):
        with patch.object(paths, "ROOT", Path("/usr/share/system-device-monitor")):
            self.assertEqual(autostart.launcher_path(), Path("/usr/bin/system-device-monitor"))
        with patch.object(paths, "ROOT", Path("/opt/checkout")):
            self.assertEqual(autostart.launcher_path(),
                             Path.home()/".local/bin/system-device-monitor")

    def test_version_fast_path_does_not_load_gi(self):
        # 以子程序執行進入點，確認 --version 不載入 gi（不需要圖形環境）。
        code = ("import runpy, sys\n"
                "sys.argv = ['device_monitor.py', '--version']\n"
                "try:\n"
                "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
                "except SystemExit as stop:\n"
                "    print('exit', stop.code, 'gi' in sys.modules)\n")
        output = subprocess.check_output([sys.executable, "-B", "-c", code], cwd=ROOT, text=True)
        version = (ROOT/"VERSION").read_text().strip()
        self.assertEqual(output.splitlines(),
                         ["System Device Monitor " + version, "exit 0 False"])

class Launcher(unittest.TestCase):
    """啟動器：一次性的指令（--dump、--charging-plan-status、--test-notification 等）把輸出印在
    終端機，讓使用者看得到；看板本身（含背景執行與示範模式）的輸出寫進快取目錄的 app.log。"""

    def run_launcher(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/"checkout"
            root.mkdir()
            (root/"device_monitor.py").write_text(
                "import sys\nprint('out', *sys.argv[1:], flush=True)\n"
                "print('err', file=sys.stderr)\n")
            launcher = Path(tmp)/"launcher"
            launcher.write_text(autostart.launcher_script(root))
            launcher.chmod(0o755)
            # 啟動器會建立快取目錄；指向暫存目錄，避免碰到真實的使用者快取。
            env = {**os.environ, "XDG_CACHE_HOME": str(Path(tmp)/"cache")}
            result = subprocess.run([str(launcher), *args], capture_output=True, text=True,
                                    env=env, timeout=30)
            log = Path(tmp)/"cache/system-device-monitor/app.log"
            return result, (log.read_text() if log.exists() else "")

    def test_one_shot_commands_print_in_the_terminal(self):
        for args in (("--version",), ("--dump",), ("--charging-plan-status",),
                     ("--test-notification",), ("--help",), ("-h",),
                     ("--smoke-test", "--output-dir", "out")):
            with self.subTest(args=args):
                result, log = self.run_launcher(*args)
                self.assertEqual((result.stdout, result.stderr),
                                 (" ".join(("out",) + args) + "\n", "err\n"))
                self.assertEqual(log, "")

    def test_the_dashboard_writes_its_output_to_the_log(self):
        for args in ((), ("--background",), ("--preview", "desktop")):
            with self.subTest(args=args):
                result, log = self.run_launcher(*args)
                self.assertEqual((result.stdout, result.stderr), ("", ""))
                self.assertEqual(log, " ".join(("out",) + args) + "\nerr\n")


class Publication(unittest.TestCase):
    def test_private_data_detection_does_not_need_real_private_data(self):
        path = ROOT/"scripts/privacy_check.py"
        spec = importlib.util.spec_from_file_location("privacy_check", path)
        privacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(privacy)
        with tempfile.TemporaryDirectory() as tmp:
            item = Path(tmp)/"sample.txt"
            item.write_text("synthetic" + "@" + "private.test\n" + ":".join(["aa"]*6))
            self.assertEqual(set(privacy.findings(item)), {"personal email", "MAC address"})
            item.write_text("nobody@users.noreply.github.com")
            self.assertEqual(privacy.findings(item), [])

if __name__ == "__main__":
    unittest.main()
