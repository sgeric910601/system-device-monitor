"""補回經由真正的 D-Bus（私人匯流排上的假 UPower）非同步讀取電量紀錄：服務不在時不自動啟動、
只讀唯一的系統電池、只補空白、每段空白只讀一次、太慢時逾時而且主迴圈不被卡住。

匯流排由 dbus-run-session 建立且不提供任何服務；絕不連到系統匯流排或真正的 UPower。
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sdm.constants import Backfill, History
from tests.fixtures.session_bus import DBUS_RUN_SESSION, clean_environment, write_config

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 90


@unittest.skipUnless(DBUS_RUN_SESSION, "needs dbus-run-session")
class RealUpower(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            config, address = write_config(temp, "sdm-upower-test-")
            result = subprocess.run(
                [DBUS_RUN_SESSION, f"--config-file={config}", "--", sys.executable, "-B", "-m",
                 "tests.fixtures.upower_bus"],
                env=clean_environment(SDM_PRIVATE_BUS=address), cwd=ROOT,
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        cls.result = result
        lines = result.stdout.strip().splitlines()
        cls.report = json.loads(lines[-1]) if result.returncode == 0 and lines else {}

    def setUp(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr[-2000:])

    def test_absent_service_is_not_started_and_counts_as_a_failure(self):
        self.assertEqual(self.report["absent"], {"busy": False, "failures": 1})

    def test_laptop_battery_history_fills_only_the_gaps(self):
        laptop = self.report["laptop"]
        self.assertTrue(laptop["asked"] and laptop["done"])
        self.assertEqual(laptop["imported"], [[7200, 65, False, True], [3600, 70, True, True]])
        self.assertEqual((laptop["spans"], laptop["own"]), (2, 2))
        self.assertEqual(laptop["history_calls"], [
            ["battery_BAT0", "pct", "charge", History.RETENTION_SECONDS, Backfill.MAX_POINTS]])

    def test_each_gap_is_read_only_once(self):
        self.assertFalse(self.report["laptop"]["again"])
        self.assertEqual(self.report["laptop"]["calls_after"], 0)

    def test_only_type_and_power_supply_are_read(self):
        self.assertEqual(self.report["properties"], ["PowerSupply", "Type"])

    def test_slow_service_times_out_without_blocking_the_main_loop(self):
        slow = self.report["slow"]
        self.assertEqual((slow["busy"], slow["failures"], slow["spans"]), (False, 1, 0))
        self.assertEqual(slow["asked"], ["ups_hiddev0"])
        self.assertLess(slow["seconds"], Backfill.DEADLINE_SECONDS)
        self.assertGreaterEqual(slow["ticks"], 10)

    def test_refuses_to_run_on_another_bus(self):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.fixtures.upower_bus"],
                                env=clean_environment(SDM_PRIVATE_BUS="unix:abstract=other"),
                                cwd=ROOT, capture_output=True, text=True,
                                timeout=TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
