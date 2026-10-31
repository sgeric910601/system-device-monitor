"""scripts/ci_leftovers.sh：完全移除套件之後確認什麼都沒留下（CI 在兩次 purge 之後執行）。

以假的根目錄（--root）執行：乾淨時結束碼 0；每一種留下的東西都讓它以非 0 結束，並印出路徑；
多個留下的項目全部印出後才結束；名稱掃描只是提示，從不讓檢查失敗。測試不需要 root，也不碰 dpkg 資料庫。
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.fixtures.helpers import ROOT

SCRIPT = ROOT/"scripts/ci_leftovers.sh"
PACKAGED = ("usr/bin/system-device-monitor", "usr/share/doc/system-device-monitor/README.md")
# 每一種會留下的東西（建立的檔案 → 應該印出的路徑）：套件列出的檔案、小程式、polkit 動作、規則檔、
# systemd 單元與 drop-in、/run、/var/lib、/var/cache 的狀態，以及 dpkg 的紀錄
LEFTOVERS = {
    "usr/bin/system-device-monitor": "/usr/bin/system-device-monitor",
    "usr/share/doc/system-device-monitor/README.md": "/usr/share/doc/system-device-monitor/README.md",
    "usr/libexec/system-device-monitor/sdm-wake": "/usr/libexec/system-device-monitor",
    "usr/local/libexec/system-device-monitor/sdm-cpu-power": "/usr/local/libexec/system-device-monitor",
    "usr/share/polkit-1/actions/tw.local.system-device-monitor.rules.policy":
        "/usr/share/polkit-1/actions/tw.local.system-device-monitor.rules.policy",
    "usr/local/share/polkit-1/actions/tw.local.system-device-monitor.local.policy":
        "/usr/local/share/polkit-1/actions/tw.local.system-device-monitor.local.policy",
    "etc/polkit-1/rules.d/49-system-device-monitor.rules":
        "/etc/polkit-1/rules.d/49-system-device-monitor.rules",
    "usr/lib/systemd/system/system-device-monitor-cpu-power.socket":
        "/usr/lib/systemd/system/system-device-monitor-cpu-power.socket",
    "etc/systemd/system/system-device-monitor-cpu-power.socket.d/owner.conf":
        "/etc/systemd/system/system-device-monitor-cpu-power.socket.d",
    "etc/systemd/system/system-device-monitor-local-charge-limit.service":
        "/etc/systemd/system/system-device-monitor-local-charge-limit.service",
    "run/system-device-monitor/wake.lock": "/run/system-device-monitor",
    "var/lib/system-device-monitor/charge-limit.json": "/var/lib/system-device-monitor",
    "var/cache/system-device-monitor/staging/.lock": "/var/cache/system-device-monitor",
    "var/lib/dpkg/info/system-device-monitor.list": "/var/lib/dpkg/info/system-device-monitor.list",
}
WANTS = "etc/systemd/system/sleep.target.wants/system-device-monitor-charge-limit.service"


@unittest.skipUnless(shutil.which("dpkg-deb") and shutil.which("bash"), "needs dpkg-deb and bash")
class Leftovers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.roots = 0
        self.root = self.fresh_root()
        self.deb = self.build_package()

    def fresh_root(self):
        """乾淨的假根目錄：有系統本來就有的目錄，沒有任何本套件的東西。"""
        self.roots += 1
        root = self.tmp/f"root{self.roots}"
        for name in ("etc/polkit-1/rules.d", "usr/share/polkit-1/actions", "var/lib/dpkg/info",
                     "run", "usr/lib/systemd/system", "etc/systemd/system/multi-user.target.wants",
                     "usr/bin", "var/cache/apt"):
            (root/name).mkdir(parents=True)
        return root

    def build_package(self):
        """只有兩個檔案的小套件，代表 .deb 列出的內容。"""
        stage = self.tmp/"stage"
        for name in PACKAGED:
            (stage/name).parent.mkdir(parents=True, exist_ok=True)
            (stage/name).write_text("x\n")
        (stage/"DEBIAN").mkdir()
        (stage/"DEBIAN/control").write_text(
            "Package: system-device-monitor\nVersion: 0.0.1\nArchitecture: all\n"
            "Maintainer: test <test@example.invalid>\nDescription: test\n")
        deb = self.tmp/"package.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(deb)],
                       check=True, capture_output=True)
        return deb

    def run_check(self):
        return subprocess.run(["bash", str(SCRIPT), "--root", str(self.root), str(self.deb)],
                              capture_output=True, text=True, timeout=120)

    def leave(self, name):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("left\n")

    def test_clean_root_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("left behind:", result.stdout)

    def test_each_kind_of_leftover_fails_with_its_path(self):
        for name, shown in LEFTOVERS.items():
            with self.subTest(name=name):
                self.root = self.fresh_root()
                self.leave(name)
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("left behind: " + shown + "\n", result.stdout)

    def test_dangling_wants_link_fails(self):
        link = self.root/WANTS
        link.parent.mkdir(parents=True)
        link.symlink_to("/usr/lib/systemd/system/system-device-monitor-charge-limit.service")
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("left behind: /" + WANTS, result.stdout)

    def test_every_leftover_is_printed_before_failing(self):
        for name in LEFTOVERS:
            self.leave(name)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        for shown in LEFTOVERS.values():
            with self.subTest(shown=shown):
                self.assertIn("left behind: " + shown + "\n", result.stdout)

    def test_name_matches_elsewhere_are_only_reported(self):
        self.leave("etc/unrelated/system-device-monitor-notes.txt")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("/etc/unrelated/system-device-monitor-notes.txt", result.stdout)
        self.assertNotIn("left behind:", result.stdout)

    def test_bad_arguments(self):
        for argv in ([], ["--root"], ["--root", str(self.root)],
                     ["--root", str(self.root), str(self.tmp/"missing.deb")]):
            with self.subTest(argv=argv):
                result = subprocess.run(["bash", str(SCRIPT), *argv], capture_output=True,
                                        text=True, timeout=60)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_script_is_executable_and_valid_bash(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        # 檢查真正的根目錄時以 sudo 讀取（rules.d 等目錄只有 root 能讀）；測試只用假的根目錄
        self.assertIn("as_root=(sudo)", SCRIPT.read_text(encoding="utf-8"))
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
