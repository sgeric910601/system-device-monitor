"""真正的 libnotify 經由私人匯流排送到假的通知服務：提示、緊急程度、預設動作與關閉訊號。

匯流排由 dbus-run-session 建立且不提供任何服務，絕不連到真正的桌面通知。
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.fixtures.session_bus import DBUS_RUN_SESSION, clean_environment, write_config

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 60
URGENCY_CRITICAL = 2


@unittest.skipUnless(DBUS_RUN_SESSION, "needs dbus-run-session")
class RealLibnotify(unittest.TestCase):
    def test_notification_reaches_a_fake_server_on_a_private_bus(self):
        with tempfile.TemporaryDirectory() as temp:
            config, address = write_config(temp, "sdm-notify-test-")
            result = subprocess.run(
                [DBUS_RUN_SESSION, f"--config-file={config}", "--", sys.executable, "-B", "-m",
                 "tests.fixtures.notification_bus"],
                env=clean_environment(SDM_PRIVATE_BUS=address), cwd=ROOT,
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual((report["before"], report["after"]), ("unavailable", "available"))
        self.assertTrue(report["sent"])
        self.assertEqual((report["app"], report["summary"], report["icon"]),
                         ("系統與裝置監看", "不斷電系統改用電池供電",
                          "uninterruptible-power-supply-symbolic"))
        self.assertEqual(report["body"], "電量 99%，負載約 297 W，預估可用約 44 分鐘。")
        self.assertEqual(report["actions"], ["default", "開啟"])
        self.assertEqual((report["desktop_entry"], report["urgency"]),
                         ("system-device-monitor", URGENCY_CRITICAL))
        self.assertEqual(report["opened"], ["power"])
        self.assertEqual((report["kept"], report["released"]), (True, True))

    def test_refuses_to_run_on_another_bus(self):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.fixtures.notification_bus"],
                                env=clean_environment(SDM_PRIVATE_BUS="unix:abstract=other"),
                                cwd=ROOT, capture_output=True, text=True,
                                timeout=TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
