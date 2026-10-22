"""示範模式實際執行一次（虛擬螢幕）：不寫入任何 XDG 目錄，也不碰正在執行的看板。

使用私人的工作階段匯流排，而且不提供任何可自動啟動的服務（桌面入口、鑰匙圈等），
只留下程式本身的行為；設定、資料、快取、狀態目錄都指向空的暫存目錄。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DBUS_RUN_SESSION = shutil.which("dbus-run-session")
XVFB_RUN = shutil.which("xvfb-run")
TIMEOUT_SECONDS = 120
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:abstract={name}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""
REMOVED = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
XDG = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")


@unittest.skipUnless(DBUS_RUN_SESSION and XVFB_RUN, "needs dbus-run-session and xvfb-run")
class PreviewRun(unittest.TestCase):
    def test_preview_writes_nothing_outside_the_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            directories = {name: base/"xdg"/name.lower() for name in XDG}
            for directory in directories.values():
                directory.mkdir(parents=True)
            output = base/"output"
            output.mkdir()
            config = base/"bus.conf"
            config.write_text(BUS_CONFIG.format(name=f"sdm-preview-test-{os.getpid()}"))
            env = {key: value for key, value in os.environ.items() if key not in REMOVED}
            env.update({name: str(path) for name, path in directories.items()})
            result = subprocess.run(
                [DBUS_RUN_SESSION, f"--config-file={config}", "--", XVFB_RUN, "-a",
                 sys.executable, "-B", str(ROOT/"device_monitor.py"), "--preview",
                 "laptop-battery", "--smoke-test", "--output-dir", str(output)],
                env=env, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            report = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual((report["ok"], report["indicator_count"]), (True, 0))
            self.assertEqual(report["pages"], ["power", "overview", "devices", "settings"])
            written = sorted(str(path.relative_to(base)) for directory in directories.values()
                             for path in directory.rglob("*"))
            self.assertEqual(written, [])
            self.assertEqual([path.name for path in output.iterdir()], ["smoke.json"])


if __name__ == "__main__":
    unittest.main()
