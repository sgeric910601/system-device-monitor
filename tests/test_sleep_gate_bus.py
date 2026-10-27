"""SleepGate 經由真正的 D-Bus（私人匯流排上的假 logind）拿到、保留、放開與重新拿延遲鎖。

匯流排由 dbus-run-session 建立且不提供任何服務；絕不連到系統匯流排或真正的 logind。
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sdm.services.sleep_gate import MODE, WHAT, WHO, WHY
from tests.fixtures.session_bus import DBUS_RUN_SESSION, clean_environment, write_config

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 60


@unittest.skipUnless(DBUS_RUN_SESSION, "needs dbus-run-session")
class RealInhibitor(unittest.TestCase):
    def test_delay_lock_round_trip_through_a_fake_logind(self):
        with tempfile.TemporaryDirectory() as temp:
            config, address = write_config(temp, "sdm-logind-test-")
            result = subprocess.run(
                [DBUS_RUN_SESSION, f"--config-file={config}", "--", sys.executable, "-B", "-m",
                 "tests.fixtures.logind_bus"],
                env=clean_environment(SDM_PRIVATE_BUS=address), cwd=ROOT,
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["call"], [WHAT, WHO, WHY, MODE])
        for key in ("took", "held", "still_held", "released", "retaken", "ended"):
            with self.subTest(step=key):
                self.assertTrue(report[key])
        self.assertEqual(report["events"], ["sleep", "wake"])

    def test_refuses_to_run_on_another_bus(self):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.fixtures.logind_bus"],
                                env=clean_environment(SDM_PRIVATE_BUS="unix:abstract=other"),
                                cwd=ROOT, capture_output=True, text=True,
                                timeout=TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
