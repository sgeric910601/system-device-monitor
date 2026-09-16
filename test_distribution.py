"""Portable installation, privacy and sensor mapping regression tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import collectors
import runtime
from model import normalize_usb

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
        with patch("collectors.read", return_value="\n\n".join(records)):
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
        with patch("collectors.Gio.bus_get_sync", side_effect=GLib.Error("unavailable")), \
             patch("collectors.load_json", return_value={}):
            collector = collectors.Collectors()
        self.assertIsNone(collector.bus)
        with self.assertRaises(GLib.Error):
            collectors.dbus(None, "", "", "", "")
        self.assertIn("ram", collector.system())

    def test_first_run_and_disabled_autostart_survive_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/"settings.json"
            auto = Path(tmp)/"autostart/app.desktop"
            with patch.multiple(runtime, CONFIG=config, AUTOSTART=auto):
                runtime.initialize_preferences()
                self.assertTrue(auto.exists())
                runtime.set_autostart(False)
                runtime.initialize_preferences()
                self.assertFalse(auto.exists())
                self.assertEqual(json.loads(config.read_text()), runtime.DEFAULTS)

    def test_launcher_handles_spaces_and_shell_metacharacters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/"checkout with spaces '$dollar"
            root.mkdir()
            (root/"app.py").write_text("import sys; print(sys.argv[1])")
            launcher = Path(tmp)/"launcher"
            launcher.write_text(runtime.launcher_script(root))
            launcher.chmod(0o755)
            result = subprocess.check_output([str(launcher), "--version"], text=True)
            self.assertEqual(result.strip(), "--version")

    def test_desktop_exec_handles_spaces_and_metacharacters(self):
        from gi.repository import Gio
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'launcher with spaces $dollar %field "quote'
            output = Path(tmp)/"result"
            target.write_text("#!/usr/bin/python3\nfrom pathlib import Path\nPath("+repr(str(output))+").write_text('ok')\n")
            target.chmod(0o755)
            entry = Path(tmp)/"monitor.desktop"
            entry.write_text(runtime.desktop_entry(launcher=target))
            subprocess.run(["desktop-file-validate", str(entry)], check=True)
            app = Gio.DesktopAppInfo.new_from_filename(str(entry))
            self.assertIsNotNone(app)
            app.launch([], None)
            import time
            for _ in range(50):
                if output.exists(): break
                time.sleep(.02)
            self.assertEqual(output.read_text(), "ok")

class Publication(unittest.TestCase):
    def test_private_data_detection_does_not_need_real_private_data(self):
        path = Path(__file__).parent/"scripts/privacy_check.py"
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
