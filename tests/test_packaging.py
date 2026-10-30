"""套件中的管理員元件：安裝位置與權限，以及 postinst／prerm／postrm。

安裝前後腳本以 DPKG_ROOT 指向暫存目錄實際執行（dpkg 以 --root 安裝時也是如此），
這時腳本不會呼叫 systemctl；PATH 裡放一個記錄呼叫的假 systemctl 確認這一點。
線上（有 systemd）的分支只做靜態檢查，實際行為由 CI 安裝、移除與清除套件時驗證。
"""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from tests.fixtures.helpers import ROOT, load_script

build = load_script("build_deb")
MANIFEST = json.loads((ROOT/"package-files.json").read_text(encoding="utf-8"))
SCRIPTS = ROOT/"packaging/debian"
SOCKET = "system-device-monitor-cpu-power.socket"
SERVICE = "system-device-monitor-cpu-power.service"
LIMIT_UNIT = "system-device-monitor-charge-limit.service"
LIMIT_POLICY = "tw.local.system-device-monitor.unattended.charge-limit.policy"
WAKE_POLICY = "tw.local.system-device-monitor.unattended.wake.policy"
RULES_POLICY = "tw.local.system-device-monitor.rules.policy"
ACTIONS = "/usr/share/polkit-1/actions/"
RULE = "etc/polkit-1/rules.d/49-system-device-monitor.rules"
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
    {"source": "helper/sdm-wake", "target": LIBEXEC + "sdm-wake", "mode": "0755"},
    {"source": "helper/policy/" + WAKE_POLICY, "target": ACTIONS + WAKE_POLICY, "mode": "0644"},
    {"source": "helper/sdm-unattended", "target": LIBEXEC + "sdm-unattended", "mode": "0755"},
    {"source": "helper/policy/" + RULES_POLICY, "target": ACTIONS + RULES_POLICY, "mode": "0644"},
] + [{"source": f"assets/app/mint-{size}.png",
      "target": f"/usr/share/icons/hicolor/{size}x{size}/apps/system-device-monitor.png",
      "mode": "0644"} for size in (128, 512)] + [  # 預設的程式圖示（薄荷綠）
    {"source": "helper/sdm-update", "target": LIBEXEC + "sdm-update", "mode": "0755"},
    {"source": "helper/policy/tw.local.system-device-monitor.update.policy",
     "target": "/usr/share/polkit-1/actions/tw.local.system-device-monitor.update.policy",
     "mode": "0644"},
]
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
                          "tw.local.system-device-monitor.unattended.local.wake.policy",
                          "tw.local.system-device-monitor.local.rules.policy",
                          "49-system-device-monitor.rules", "charge-limit.json"):
                self.assertNotIn(local, names)
            self.assertEqual(sorted(path.name for path in (stage/LIBEXEC.strip("/")).iterdir()),
                             ["sdm-charge-limit", "sdm-cpu-power", "sdm-cpu-power-service",
                              "sdm-unattended", "sdm-update", "sdm-wake"])


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
                if name == "postinst":
                    self.assertNotIn("/var/lib", self.text(name))

    def test_remove_and_purge_delete_only_the_charge_limit_state_file(self):
        for name in ("prerm", "postrm"):
            with self.subTest(script=name):
                lines = [line.strip() for line in self.text(name).splitlines()
                         if ("/var/lib" in line or "STATE_DIR" in line)
                         and not line.startswith("#")]
                self.assertEqual(lines, [
                    'STATE_DIR="${DPKG_ROOT:-}/var/lib/system-device-monitor"',
                    'rm -f "$STATE_DIR/charge-limit.json"',
                    'rmdir "$STATE_DIR" 2>/dev/null || true'])

    def test_smart_charging_part_is_the_same_in_prerm_and_postrm(self):
        start, end = "# ── 智慧充電：prerm 與 postrm 共用", "# ── 智慧充電共用部分結束"
        parts = []
        for name in ("prerm", "postrm"):
            text = self.text(name)
            parts.append(text[text.index(start):text.index(end)])
        self.assertEqual(parts[0], parts[1])
        self.assertIn("with_lock unattended.lock revoke_rule", parts[0])
        self.assertLess(parts[0].index("with_lock unattended.lock"),
                        parts[0].index("with_lock wake.lock"))

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
        self.assertIn("charge-limit.json", text)

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

    def test_prerm_remove_disables_the_charge_limit_unit_and_deletes_its_state(self):
        self.populate_limit()
        self.run_script("prerm", "remove")
        self.assertFalse(any(os.path.lexists(link) for link in self.limit_wants))
        self.assertFalse(self.state.exists())

    def test_lock_files_are_deleted_by_postrm_after_the_helpers_are_gone(self):
        """刪除還有人在等的鎖檔會讓兩個小程式同時執行：prerm 留著，postrm（小程式已刪除）才刪。"""
        run = self.root/"run/system-device-monitor"
        run.mkdir(parents=True)
        names = ("charge-limit.lock", "charge-limit.times", "wake.lock", "wake.times",
                 "unattended.lock")
        for name in names:
            (run/name).write_text("")
        self.run_script("prerm", "remove")
        self.assertEqual(sorted(path.name for path in run.iterdir()), sorted(names))
        self.run_script("postrm", "remove")
        self.assertFalse(run.exists())

    def test_remove_keeps_other_runtime_files(self):
        run = self.root/"run/system-device-monitor"
        run.mkdir(parents=True)
        (run/"charge-limit.lock").write_text("")
        (run/"other").write_text("")
        self.run_script("prerm", "remove")
        self.run_script("postrm", "remove")
        self.assertEqual(sorted(path.name for path in run.iterdir()), ["other"])

    def test_prerm_remove_keeps_other_state_files(self):
        self.populate_limit()
        local = self.state/"charge-limit-local.json"
        local.write_text("{}\n")
        self.run_script("prerm", "remove")
        self.assertFalse((self.state/"charge-limit.json").exists())
        self.assertTrue(local.exists())

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

    def test_postrm_purge_removes_only_empty_update_staging(self):
        staging = self.root/"var/cache/system-device-monitor/staging"
        staging.mkdir(parents=True)
        self.run_script("postrm", "purge")
        self.assertFalse((self.root/"var/cache/system-device-monitor").exists())
        staging.mkdir(parents=True)
        (staging/".lock").write_text("")
        self.run_script("postrm", "purge")
        self.assertFalse((self.root/"var/cache/system-device-monitor").exists())
        (staging/"0123456789abcdef").mkdir(parents=True)
        self.run_script("postrm", "purge")
        self.assertTrue((staging/"0123456789abcdef").exists())
        self.run_script("postrm", "remove")
        self.assertTrue(staging.exists())

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

    def populate_wake(self, alarm, record):
        rtc = self.root/"sys/class/rtc/rtc0"
        rtc.mkdir(parents=True)
        (rtc/"wakealarm").write_text(alarm)
        run = self.root/"run/system-device-monitor"
        run.mkdir(parents=True)
        (run/"wake").write_text(record)
        for name in ("wake.lock", "wake.times", "unattended.lock"):
            (run/name).write_text("")
        return rtc/"wakealarm", run

    def test_prerm_remove_clears_only_the_recorded_alarm(self):
        alarm, run = self.populate_wake("1800000600\n", "1800000600\n")
        self.run_script("prerm", "remove")
        self.assertEqual(alarm.read_text(), "0")
        self.assertFalse((run/"wake").exists())
        self.run_script("postrm", "remove")
        self.assertFalse(run.exists())

    def test_prerm_remove_leaves_another_alarm(self):
        for record in ("1800000600\n", "garbage\n", "", "0\n"):
            with self.subTest(record=record):
                shutil.rmtree(self.root, ignore_errors=True)
                alarm, run = self.populate_wake("1800009999\n", record)
                self.run_script("prerm", "remove")
                self.run_script("postrm", "remove")
                self.assertEqual(alarm.read_text(), "1800009999\n")
                self.assertFalse(run.exists())

    def test_prerm_upgrade_keeps_the_alarm_and_the_rule(self):
        alarm, run = self.populate_wake("1800000600\n", "1800000600\n")
        rule = self.root/RULE
        rule.parent.mkdir(parents=True)
        rule.write_text("// rule\n")
        self.run_script("prerm", "upgrade", "1.0")
        self.assertEqual(alarm.read_text(), "1800000600\n")
        self.assertTrue((run/"wake").exists() and rule.exists())

    def populate_smart_charging(self):
        alarm, run = self.populate_wake("1800000600\n", "1800000600\n")
        rule = self.root/RULE
        rule.parent.mkdir(parents=True)
        rule.write_text("// rule\n")
        self.populate_limit()
        return alarm, run, rule

    def test_prerm_downgrade_without_smart_charging_revokes_the_rule_and_alarm(self):
        """降級到沒有 sdm-unattended 的版本：那個版本不會再管理規則與鬧鐘，所以和移除時一樣清掉。"""
        for older in ("0.2.0", "0.2.9", "0.3.0~~", "0.1.0", ""):
            with self.subTest(older=older):
                shutil.rmtree(self.root, ignore_errors=True)
                alarm, run, rule = self.populate_smart_charging()
                self.run_script("prerm", "upgrade", older)
                self.assertFalse(rule.exists())
                self.assertEqual(alarm.read_text(), "0")
                self.assertFalse((run/"wake").exists())
                self.assertTrue(all(os.path.lexists(link) for link in self.limit_wants))
                self.assertTrue((self.state/"charge-limit.json").exists())

    def test_prerm_upgrade_to_a_version_with_smart_charging_keeps_the_rule(self):
        for newer in ("0.3.0~", "0.3.0~beta.1", "0.3.0~beta.12", "0.3.0", "0.3.1", "1.0"):
            with self.subTest(newer=newer):
                shutil.rmtree(self.root, ignore_errors=True)
                alarm, run, rule = self.populate_smart_charging()
                self.run_script("prerm", "upgrade", newer)
                self.assertTrue(rule.exists())
                self.assertEqual(alarm.read_text(), "1800000600\n")
                self.assertTrue((run/"wake").exists())

    def test_prerm_revokes_the_rule_then_waits_for_the_wake_lock(self):
        """先撤銷規則，再等正在執行的 sdm-wake 放開鎖（flock），才取消鬧鐘並刪除紀錄。"""
        alarm, run, rule = self.populate_smart_charging()
        fd = os.open(run/"wake.lock", os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "DPKG_ROOT": str(self.root)}
        process = subprocess.Popen(["/bin/sh", str(SCRIPTS/"prerm"), "remove"], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.wait, 30)
        deadline = time.monotonic() + 10
        while rule.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(rule.exists())
        time.sleep(0.3)
        self.assertIsNone(process.poll())
        self.assertEqual((alarm.read_text(), (run/"wake").exists()), ("1800000600\n", True))
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(process.wait(timeout=20), 0, process.stderr.read())
        process.stderr.close()
        self.assertEqual(alarm.read_text(), "0")
        self.assertFalse((run/"wake").exists())

    def test_prerm_waits_for_a_running_grant_before_revoking(self):
        """正在執行的 sdm-unattended grant（已輸入管理員密碼）寫完規則之後，才撤銷規則。"""
        alarm, run, rule = self.populate_smart_charging()
        fd = os.open(run/"unattended.lock", os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "DPKG_ROOT": str(self.root)}
        process = subprocess.Popen(["/bin/sh", str(SCRIPTS/"prerm"), "remove"], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.wait, 30)
        time.sleep(0.5)
        self.assertIsNone(process.poll())
        self.assertTrue(rule.exists())
        rule.write_text("// written by the grant that was running\n")
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(process.wait(timeout=20), 0, process.stderr.read())
        process.stderr.close()
        self.assertFalse(rule.exists())

    def test_postrm_remove_cancels_what_a_late_helper_left(self):
        """prerm 之後才完成的 sdm-wake set 或 grant（授權在規則刪除前就給了）：小程式刪除後再清一次。"""
        alarm, run, rule = self.populate_smart_charging()
        self.run_script("prerm", "remove")
        alarm.write_text("1800009000\n")
        (run/"wake").write_text("1800009000\n")
        rule.write_text("// late grant\n")
        self.run_script("postrm", "remove")
        self.assertEqual(alarm.read_text(), "0")
        self.assertFalse(rule.exists())
        self.assertFalse(run.exists())

    def test_downgrade_is_best_effort(self):
        """降級時撤銷失敗（例如 /etc 唯讀）也讓降級繼續（失敗會改跑舊版的 prerm，規則就留下）；
        小程式換掉之後 postrm 再清一次。"""
        alarm, run, rule = self.populate_smart_charging()
        rule.parent.chmod(0o555)
        self.addCleanup(rule.parent.chmod, 0o755)
        result = self.run_script("prerm", "upgrade", "0.2.0")
        self.assertEqual(result.stderr.count("無法刪除"), 1, result.stderr)
        self.assertIn("49-system-device-monitor.rules", result.stderr)
        self.assertTrue(rule.exists())
        self.assertEqual(alarm.read_text(), "0")
        rule.parent.chmod(0o755)
        alarm.write_text("1800009000\n")
        (run/"wake").write_text("1800009000\n")
        self.run_script("postrm", "upgrade", "0.2.0")
        self.assertFalse(rule.exists())
        self.assertEqual(alarm.read_text(), "0")
        self.assertTrue((run/"wake.lock").exists())

    def test_postrm_upgrade_to_a_version_with_smart_charging_keeps_everything(self):
        alarm, run, rule = self.populate_smart_charging()
        self.run_script("postrm", "upgrade", "0.3.0~beta.1")
        self.assertTrue(rule.exists() and (run/"wake").exists())
        self.assertEqual(alarm.read_text(), "1800000600\n")

    def test_remove_and_purge_delete_only_the_unattended_rule(self):
        rules = self.root/RULE
        rules.parent.mkdir(parents=True)
        other = rules.parent/"50-other.rules"
        other.write_text("// other\n")
        for script, args in (("prerm", ("remove",)), ("postrm", ("remove",)),
                             ("postrm", ("purge",))):
            with self.subTest(script=script, args=args):
                rules.write_text("// rule\n")
                self.run_script(script, *args)
                self.assertFalse(rules.exists())
                self.assertTrue(other.exists())

    def test_rule_name_that_is_a_directory_is_left_alone(self):
        rules = self.root/RULE
        rules.mkdir(parents=True)
        self.run_script("prerm", "remove")
        self.run_script("postrm", "purge")
        self.assertTrue(rules.is_dir())

    def test_postinst_warns_about_a_source_install(self):
        helper = self.root/"usr/local/libexec/system-device-monitor/sdm-cpu-power"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/bin/sh\n")
        result = self.run_script("postinst", "configure", "")
        self.assertLess(result.stderr.index("sudo apt remove system-device-monitor"),
                        result.stderr.index("install.py --system-components remove"))


if __name__ == "__main__":
    unittest.main()
