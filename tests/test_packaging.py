"""套件中的管理員元件：安裝位置與權限，以及 postinst／prerm／postrm。

安裝前後腳本以 DPKG_ROOT 指向暫存目錄實際執行（dpkg 以 --root 安裝時也是如此），
這時腳本不會呼叫 systemctl；PATH 裡放一個記錄呼叫的假 systemctl 確認這一點。
線上（有 systemd）的分支只做靜態檢查，實際行為由 CI 安裝、移除與清除套件時驗證。
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests.fixtures.helpers import ROOT, load_script

build = load_script("build_deb")
MANIFEST = json.loads((ROOT/"package-files.json").read_text(encoding="utf-8"))
SCRIPTS = ROOT/"packaging/debian"
SOCKET = "system-device-monitor-cpu-power.socket"
SERVICE = "system-device-monitor-cpu-power.service"
LIMIT_UNIT = "system-device-monitor-charge-limit.service"
LIMIT_POLICY = "tw.local.system-device-monitor.unattended.charge-limit.policy"
LIBEXEC = "/usr/libexec/system-device-monitor/"
UNITS = "/usr/lib/systemd/system/"
SYSTEM = [
    {"source": "helper/sdm-cpu-power", "target": LIBEXEC + "sdm-cpu-power", "mode": "0755"},
    {"source": "helper/sdm-cpu-power-service", "target": LIBEXEC + "sdm-cpu-power-service",
     "mode": "0755"},
    {"source": "helper/policy/tw.local.system-device-monitor.policy",
     "target": "/usr/share/polkit-1/actions/tw.local.system-device-monitor.policy",
     "mode": "0644"},
    {"source": "helper/systemd/" + SOCKET, "target": UNITS + SOCKET, "mode": "0644"},
    {"source": "helper/systemd/" + SERVICE, "target": UNITS + SERVICE, "mode": "0644"},
    {"source": "helper/sdm-charge-limit", "target": LIBEXEC + "sdm-charge-limit", "mode": "0755"},
    {"source": "helper/policy/" + LIMIT_POLICY,
     "target": "/usr/share/polkit-1/actions/" + LIMIT_POLICY, "mode": "0644"},
    {"source": "helper/systemd/" + LIMIT_UNIT, "target": UNITS + LIMIT_UNIT, "mode": "0644"},
] + [{"source": f"assets/app/mint-{size}.png",
      "target": f"/usr/share/icons/hicolor/{size}x{size}/apps/system-device-monitor.png",
      "mode": "0644"} for size in (128, 512)]      # 預設的程式圖示（薄荷綠）
MAINTAINER = {name: "packaging/debian/" + name for name in ("postinst", "prerm", "postrm")}
ALLOWED_SYSTEMCTL = {"daemon-reload", "is-enabled", "stop", "restart", "disable"}


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


class Manifest(unittest.TestCase):
    def test_system_entries(self):
        self.assertEqual(MANIFEST["system"], SYSTEM)

    def test_maintainer_scripts(self):
        self.assertEqual(MANIFEST["maintainer"], MAINTAINER)

    def test_staged_package_has_only_the_package_variants(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)
            build.stage_package(MANIFEST, stage, root=ROOT, version="9.9.9", epoch=0)
            for entry in SYSTEM:
                with self.subTest(target=entry["target"]):
                    path = stage/entry["target"].lstrip("/")
                    self.assertEqual(oct(mode(path)), oct(int(entry["mode"], 8)))
                    self.assertEqual(path.read_bytes(), (ROOT/entry["source"]).read_bytes())
            for name in MAINTAINER:
                self.assertEqual(oct(mode(stage/"DEBIAN"/name)), oct(0o755))
            names = {path.name for path in stage.rglob("*")}
            for local in ("tw.local.system-device-monitor.local.policy", "install_system.py",
                          "system-device-monitor-local-cpu-power.socket", "owner.conf",
                          "tw.local.system-device-monitor.unattended.local.charge-limit.policy",
                          "system-device-monitor-local-charge-limit.service",
                          "charge-limit.json"):
                self.assertNotIn(local, names)
            self.assertEqual(sorted(path.name for path in (stage/LIBEXEC.strip("/")).iterdir()),
                             ["sdm-charge-limit", "sdm-cpu-power", "sdm-cpu-power-service"])


class ScriptText(unittest.TestCase):
    def text(self, name):
        return (SCRIPTS/name).read_text(encoding="utf-8")

    def test_posix_shell_syntax(self):
        shells = [shell for shell in ("/bin/sh", shutil.which("dash")) if shell]
        for name in MAINTAINER:
            for shell in shells:
                with self.subTest(script=name, shell=shell):
                    subprocess.run([shell, "-n", str(SCRIPTS/name)], check=True)

    def test_header_strict_mode_and_exit(self):
        for name in MAINTAINER:
            with self.subTest(script=name):
                text = self.text(name)
                self.assertTrue(text.startswith("#!/bin/sh\n"))
                self.assertIn("\nset -e\n", text)
                self.assertTrue(text.endswith("\nexit 0\n"))
                self.assertTrue(os.access(SCRIPTS/name, os.X_OK))

    def test_systemctl_never_enables_or_starts_anything(self):
        for name in MAINTAINER:
            calls = re.findall(r"systemctl(?: --quiet)? ([a-z-]+)", self.text(name))
            with self.subTest(script=name, calls=calls):
                self.assertLessEqual(set(calls), ALLOWED_SYSTEMCTL)
                self.assertNotIn("start ", self.text(name).replace("restart ", ""))

    def test_only_postinst_restarts_and_only_when_enabled(self):
        text = self.text("postinst")
        self.assertLess(text.index("is-enabled"), text.index("restart"))
        self.assertNotIn("restart", self.text("prerm") + self.text("postrm"))

    def test_scripts_never_delete_directories_recursively(self):
        for name in MAINTAINER:
            with self.subTest(script=name):
                self.assertNotIn("rm -r", self.text(name))
                if name != "postrm":
                    self.assertNotIn("/var/lib", self.text(name))

    def test_purge_removes_only_the_charge_limit_state_file(self):
        lines = [line.strip() for line in self.text("postrm").splitlines()
                 if ("/var/lib" in line or "STATE_DIR" in line) and not line.startswith("#")]
        self.assertEqual(lines, ['STATE_DIR="${DPKG_ROOT:-}/var/lib/system-device-monitor"',
                                 'rm -f "$STATE_DIR/charge-limit.json"',
                                 'rmdir "$STATE_DIR" 2>/dev/null || true'])

    def test_unit_names_match_the_shipped_units(self):
        for name in MAINTAINER:
            text = self.text(name)
            self.assertIn(f"SOCKET={SOCKET}\n", text)
            if "SERVICE=" in text:
                self.assertIn(f"SERVICE={SERVICE}\n", text)
            if "LIMIT_UNIT=" in text:
                self.assertIn(f"LIMIT_UNIT={LIMIT_UNIT}\n", text)

    def test_prerm_disables_the_charge_limit_unit(self):
        text = self.text("prerm")
        self.assertIn('systemctl disable "$LIMIT_UNIT"', text)
        self.assertNotIn("charge-limit.json", text)

    def test_live_systemd_is_skipped_for_an_offline_root(self):
        for name in MAINTAINER:
            text = self.text(name)
            for line in text.splitlines():
                if line.strip().startswith("if") and "/run/systemd/system" in line:
                    self.assertIn('[ -z "${DPKG_ROOT:-}" ]', line)


class ScriptRuns(unittest.TestCase):
    """以假根目錄執行；假 systemctl 被呼叫時把參數寫進紀錄檔。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root, self.bin, self.log = base/"root", base/"bin", base/"systemctl.log"
        self.bin.mkdir()
        fake = self.bin/"systemctl"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> '{self.log}'\n")
        fake.chmod(0o755)
        self.dropin = self.root/"etc/systemd/system"/(SOCKET + ".d")
        self.wants = self.root/"etc/systemd/system/sockets.target.wants"/SOCKET
        self.state = self.root/"var/lib/system-device-monitor"
        self.limit_wants = [self.root/"etc/systemd/system"/(target + ".wants")/LIMIT_UNIT
                            for target in ("multi-user.target", "sleep.target")]

    def populate(self):
        self.dropin.mkdir(parents=True)
        (self.dropin/"owner.conf").write_text("[Socket]\nSocketUser=1000\n")
        self.wants.parent.mkdir(parents=True)
        self.wants.symlink_to("/usr/lib/systemd/system/" + SOCKET)
        (self.state/"cache").mkdir(parents=True)
        (self.state/"cache/file").write_text("x\n")

    def run_script(self, name, *args):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "DPKG_ROOT": str(self.root)}
        result = subprocess.run(["/bin/sh", str(SCRIPTS/name), *args], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists(), "systemctl must not run for an offline root")
        return result

    def test_prerm_remove_cleans_the_dropin_and_the_wants_link(self):
        self.populate()
        self.run_script("prerm", "remove")
        self.assertFalse(self.dropin.exists())
        self.assertFalse(os.path.lexists(self.wants))
        self.assertTrue(self.state.exists())

    def populate_limit(self):
        for link in self.limit_wants:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to("/usr/lib/systemd/system/" + LIMIT_UNIT)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state/"charge-limit.json").write_text("{}\n")

    def test_prerm_remove_disables_the_charge_limit_unit_but_keeps_its_state(self):
        self.populate_limit()
        self.run_script("prerm", "remove")
        self.assertFalse(any(os.path.lexists(link) for link in self.limit_wants))
        self.assertTrue((self.state/"charge-limit.json").exists())

    def test_prerm_upgrade_keeps_the_charge_limit_unit(self):
        self.populate_limit()
        self.run_script("prerm", "upgrade", "1.0")
        self.assertTrue(all(os.path.lexists(link) for link in self.limit_wants))

    def test_postrm_purge_removes_the_charge_limit_state(self):
        self.populate_limit()
        self.run_script("postrm", "purge")
        self.assertFalse(self.state.exists())

    def test_postrm_purge_keeps_other_state_files(self):
        self.populate_limit()
        local = self.state/"charge-limit-local.json"
        local.write_text("{}\n")
        self.run_script("postrm", "purge")
        self.assertFalse((self.state/"charge-limit.json").exists())
        self.assertTrue(local.exists())

    def test_postrm_remove_keeps_the_charge_limit_state(self):
        self.populate_limit()
        self.run_script("postrm", "remove")
        self.assertTrue((self.state/"charge-limit.json").exists())

    def test_prerm_upgrade_keeps_everything(self):
        self.populate()
        self.run_script("prerm", "upgrade", "1.0")
        self.assertTrue((self.dropin/"owner.conf").exists())
        self.assertTrue(os.path.lexists(self.wants))

    def test_postrm_purge_removes_only_our_dropin(self):
        self.populate()
        other = self.dropin/"admin.conf"
        other.write_text("[Socket]\n")
        self.run_script("postrm", "purge")
        self.assertFalse((self.dropin/"owner.conf").exists())
        self.assertTrue(other.exists())
        self.assertTrue((self.state/"cache/file").exists())     # 套件從未建立，不屬於它

    def test_postrm_remove_keeps_state(self):
        self.populate()
        self.run_script("postrm", "remove")
        self.assertTrue(self.state.exists())
        self.assertTrue((self.dropin/"owner.conf").exists())

    def test_scripts_succeed_on_an_empty_root(self):
        self.root.mkdir()
        for name, args in (("postinst", ("configure", "")), ("prerm", ("remove",)),
                           ("postrm", ("remove",)), ("postrm", ("purge",))):
            with self.subTest(script=name, args=args):
                self.run_script(name, *args)
        self.assertEqual(sorted(path.name for path in self.root.rglob("*")), [])

    def test_postinst_warns_about_a_source_install(self):
        helper = self.root/"usr/local/libexec/system-device-monitor/sdm-cpu-power"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/bin/sh\n")
        result = self.run_script("postinst", "configure", "")
        self.assertIn("install.py --system-components remove", result.stderr)


if __name__ == "__main__":
    unittest.main()
