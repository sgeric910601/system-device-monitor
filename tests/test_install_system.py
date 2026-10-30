"""原始碼安裝的系統元件（scripts/install_system.py，以 root 經 pkexec 執行）。

以假根目錄（擁有者＝執行測試的使用者）代替 /，dpkg-query 與 systemctl 以假函式代替；
暫存目錄的內容與雜湊和 install.py 產生的格式相同。
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from sdm.constants import ChargeLimit, CpuPower, Unattended, Wake
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import ROOT, load_helper, load_script

installer = load_script("install_system")
control = load_helper("sdm-cpu-power")
limit = load_helper("sdm-charge-limit")
wake = load_helper("sdm-wake")
unattended = load_helper("sdm-unattended")
SOCKET = "system-device-monitor-local-cpu-power.socket"
SERVICE = "system-device-monitor-local-cpu-power.service"
LIMIT_UNIT = "system-device-monitor-local-charge-limit.service"
LIMIT_POLICY = "tw.local.system-device-monitor.unattended.local.charge-limit.policy"
LIMIT_STATE = "var/lib/system-device-monitor/charge-limit-local.json"
WAKE_POLICY = "tw.local.system-device-monitor.unattended.local.wake.policy"
RULES_POLICY = "tw.local.system-device-monitor.local.rules.policy"
RULE = "etc/polkit-1/rules.d/49-system-device-monitor.rules"
ALARM = "sys/devices/pnp0/00:00/rtc/rtc0/wakealarm"
TARGETS = {
    "usr/local/libexec/system-device-monitor/sdm-cpu-power": 0o755,
    "usr/local/libexec/system-device-monitor/sdm-cpu-power-service": 0o755,
    "usr/local/share/polkit-1/actions/tw.local.system-device-monitor.local.policy": 0o644,
    "etc/systemd/system/" + SOCKET: 0o644,
    "etc/systemd/system/" + SERVICE: 0o644,
    "usr/local/libexec/system-device-monitor/sdm-charge-limit": 0o755,
    "usr/local/share/polkit-1/actions/" + LIMIT_POLICY: 0o644,
    "etc/systemd/system/" + LIMIT_UNIT: 0o644,
    "usr/local/libexec/system-device-monitor/sdm-wake": 0o755,
    "usr/local/share/polkit-1/actions/" + WAKE_POLICY: 0o644,
    "usr/local/libexec/system-device-monitor/sdm-unattended": 0o755,
    "usr/local/share/polkit-1/actions/" + RULES_POLICY: 0o644,
}
SYSTEMCTL = "/usr/bin/systemctl"
DPKG_QUERY = "/usr/bin/dpkg-query"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Runner:
    """假的 dpkg-query 與 systemctl：回傳（結束碼、標準輸出）並記錄呼叫。"""

    def __init__(self, package_status=None, enabled=False):
        self.calls, self.package_status, self.enabled = [], package_status, enabled
        self.dpkg_result, self.on_call = None, None

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        if self.on_call is not None:
            self.on_call(argv)
        if argv[0] == DPKG_QUERY:
            if self.dpkg_result is not None:
                return self.dpkg_result
            return (0, self.package_status) if self.package_status else (1, "")
        if argv[1] == "is-enabled":
            return (0 if self.enabled else 1), ""
        return 0, ""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot(self)
        for directory in ("usr/local/share", "etc/systemd/system"):
            self.root.directory(directory)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.stage = Path(temp.name)/"stage"
        self.stage.mkdir(mode=0o700)
        self.contents = {source: (ROOT/source).read_bytes() for source, _, _ in installer.FILES}
        for name, data in self.contents.items():
            path = self.stage/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.digest = self.write_manifest({name: sha256(data)
                                           for name, data in self.contents.items()})
        self.runner = Runner()
        self.output = []
        self.uid = os.getuid()
        self.now, self.sleeps, self.on_sleep = 0.0, [], None

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()

    def write_manifest(self, files, extra=None):
        body = {"format": 1, "files": files, **(extra or {})}
        data = json.dumps(body, sort_keys=True).encode()
        (self.stage/"manifest.json").write_bytes(data)
        return sha256(data)

    def context(self, euid=0, lstat=os.lstat):
        return installer.Context(root=str(self.root.path), owner=os.getuid(), run=self.runner,
                                 euid=euid, user_exists=lambda uid: True, lstat=lstat,
                                 sleep=self.sleep, clock=lambda: self.now)

    def main(self, action="install", digest=None, env=None, euid=0, argv=None, lstat=os.lstat):
        argv = argv if argv is not None else [action, "--stage", str(self.stage),
                                              "--manifest-sha256", digest or self.digest]
        env = {"PKEXEC_UID": str(self.uid)} if env is None else env
        return installer.main(argv, env, self.context(euid, lstat), emit=self.output.append)

    def installed(self):
        return sorted(str(path.relative_to(self.root.path)) for path in self.root.path.rglob("*")
                      if path.is_file() or path.is_symlink())

    def systemctl_calls(self):
        return [call[1:] for call in self.runner.calls if call[0] == SYSTEMCTL]


class Install(Base):
    def test_copies_every_file_with_its_mode(self):
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "install"}])
        self.assertEqual(self.installed(), sorted(TARGETS))
        for target, mode in TARGETS.items():
            with self.subTest(target=target):
                self.assertEqual(self.root.mode(target), mode)
        for directory in ("usr/local/libexec", "usr/local/libexec/system-device-monitor",
                          "usr/local/share/polkit-1", "usr/local/share/polkit-1/actions"):
            self.assertEqual(self.root.mode(directory), 0o755)
        helper = self.root.path/"usr/local/libexec/system-device-monitor/sdm-cpu-power"
        self.assertEqual(helper.read_bytes(), self.contents["helper/sdm-cpu-power"])

    def test_new_directories_are_world_readable_whatever_the_umask(self):
        previous = os.umask(0o077)
        try:
            self.assertEqual(self.main(), installer.EXIT_OK)
        finally:
            os.umask(previous)
        for directory in ("usr/local/libexec", "usr/local/libexec/system-device-monitor",
                          "usr/local/share/polkit-1", "usr/local/share/polkit-1/actions"):
            self.assertEqual(self.root.mode(directory), 0o755)
        for target, mode in TARGETS.items():
            self.assertEqual(self.root.mode(target), mode)

    def test_existing_directories_keep_their_mode(self):
        self.root.directory("usr/local/libexec")
        os.chmod(self.root.path/"usr/local/libexec", 0o711)
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.assertEqual(self.root.mode("usr/local/libexec"), 0o711)

    def test_reloads_systemd_without_enabling_anything(self):
        self.main()
        self.assertEqual(self.systemctl_calls(), [("daemon-reload",),
                                                  ("is-enabled", "--quiet", SOCKET)])

    def test_restarts_an_enabled_socket(self):
        self.runner.enabled = True
        self.main()
        self.assertEqual(self.systemctl_calls()[-2:], [("stop", SERVICE), ("restart", SOCKET)])

    def test_reinstall_replaces_older_files(self):
        old = self.root.file("etc/systemd/system/" + SOCKET, "old\n")
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.assertNotEqual(old.read_text(), "old\n")

    def test_refuses_while_the_package_is_installed(self):
        for status in ("install ok installed", "install ok unpacked", "hold ok installed"):
            with self.subTest(status=status):
                self.runner.package_status = status
                self.assertEqual(self.main(), installer.EXIT_FAILED)
                self.assertEqual(self.output[-1], {"ok": False, "error": "deb-installed"})
                self.assertEqual(self.installed(), [])

    def test_unknown_package_state_is_refused(self):
        """dpkg-query 不是「找不到套件」（結束碼 1）而失敗，或回答看不懂時，不能判斷是否兩者擇一：拒絕。"""
        for result in ((2, ""), (-1, ""), (0, "garbage"), (0, "")):
            for action in ("install", "remove"):
                with self.subTest(result=result, action=action):
                    self.runner.dpkg_result = result
                    self.output.clear()
                    self.assertEqual(self.main(action), installer.EXIT_FAILED)
                    self.assertEqual(self.output, [{"ok": False, "error": "package-check-failed"}])
        self.assertEqual(self.installed(), [])

    def test_removed_package_does_not_block(self):
        for status in ("deinstall ok config-files", "unknown ok not-installed"):
            with self.subTest(status=status):
                self.runner.package_status = status
                self.assertEqual(self.main(), installer.EXIT_OK)


class Verification(Base):
    def assert_refused(self, **options):
        self.output.clear()
        self.assertEqual(self.main(**options), installer.EXIT_FAILED)
        self.assertEqual(self.installed(), [])
        self.assertEqual(self.systemctl_calls(), [])

    def test_manifest_hash_mismatch(self):
        self.assert_refused(digest="0" * 64)

    def test_file_hash_mismatch_writes_nothing(self):
        (self.stage/"helper/systemd"/SERVICE).write_bytes(b"[Service]\nExecStart=/bin/sh\n")
        self.assert_refused()

    def test_manifest_must_list_exactly_the_known_files(self):
        files = {name: sha256(data) for name, data in self.contents.items()}
        cases = (dict(files, **{"helper/extra": "0" * 64}),
                 {name: value for name, value in files.items() if name != "helper/sdm-cpu-power"},
                 dict(files, **{"helper/sdm-cpu-power": "ZZ" * 32}))
        for case in cases:
            with self.subTest(keys=sorted(case)):
                self.assert_refused(digest=self.write_manifest(case))

    def test_manifest_format_is_checked(self):
        files = {name: sha256(data) for name, data in self.contents.items()}
        self.assert_refused(digest=self.write_manifest(files, {"format": 2}))
        data = b"not json"
        (self.stage/"manifest.json").write_bytes(data)
        self.assert_refused(digest=sha256(data))

    def test_symlinked_file_in_the_stage(self):
        path = self.stage/"helper/sdm-cpu-power"
        real = self.stage.parent/"elsewhere"
        real.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(real)
        self.assert_refused()

    def test_symlinked_directory_in_the_stage(self):
        policy = self.stage/"helper/policy"
        moved = self.stage.parent/"policy"
        policy.rename(moved)
        policy.symlink_to(moved)
        self.assert_refused()

    def test_stage_must_be_private_and_owned_by_the_caller(self):
        self.stage.chmod(0o770)
        self.assert_refused()
        self.stage.chmod(0o700)
        self.uid += 1
        self.assert_refused()

    def test_symlinked_stage(self):
        link = self.stage.parent/"link"
        link.symlink_to(self.stage)
        self.assertEqual(installer.main(["install", "--stage", str(link), "--manifest-sha256",
                                         self.digest], {"PKEXEC_UID": str(self.uid)},
                                        self.context(), emit=self.output.append),
                         installer.EXIT_FAILED)
        self.assertEqual(self.installed(), [])

    def test_unsafe_stage_parent_is_refused(self):
        parent = str(self.stage.parent)
        for change in ({"st_mode": 0o40775, "st_uid": os.getuid()},
                       {"st_mode": 0o40755, "st_uid": os.getuid() + 3},
                       {"st_mode": 0o40777, "st_uid": 0}):
            def lstat(path, change=change):
                info = os.lstat(path)
                if str(path) == parent:
                    return SimpleNamespace(st_mode=change["st_mode"], st_uid=change["st_uid"])
                return info
            with self.subTest(change=change):
                self.assert_refused(lstat=lstat)

    def test_sticky_root_parent_is_accepted(self):
        parent = str(self.stage.parent)

        def lstat(path):
            if str(path) == parent:
                return SimpleNamespace(st_mode=0o41777, st_uid=0)
            return os.lstat(path)
        self.assertEqual(self.main(lstat=lstat), installer.EXIT_OK)

    def test_fifo_in_the_stage_does_not_block(self):
        path = self.stage/"helper/sdm-cpu-power"
        path.unlink()
        os.mkfifo(path, 0o600)
        results = []
        thread = threading.Thread(target=lambda: results.append(self.main()), daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [installer.EXIT_FAILED])
        self.assertEqual(self.installed(), [])

    def test_deeply_nested_manifest_is_one_json_error(self):
        data = b"[" * 200000 + b"]" * 200000
        (self.stage/"manifest.json").write_bytes(data)
        self.assertEqual(self.main(digest=sha256(data)), installer.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "invalid-manifest"}])

    def test_oversized_file(self):
        big = b"x" * (installer.MAX_FILE_BYTES + 1)
        (self.stage/"helper/sdm-cpu-power").write_bytes(big)
        files = {name: sha256(data) for name, data in self.contents.items()}
        files["helper/sdm-cpu-power"] = sha256(big)
        self.assert_refused(digest=self.write_manifest(files))

    def test_untrusted_destination(self):
        os.chmod(self.root.path/"etc/systemd/system", 0o775)
        self.assertEqual(self.main(), installer.EXIT_FAILED)
        self.assertEqual([name for name in self.installed() if name.startswith("etc/")], [])

    def test_destination_of_the_wrong_type(self):
        self.root.directory("etc/systemd/system/" + SOCKET)
        self.assertEqual(self.main(), installer.EXIT_FAILED)


class Remove(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.runner.calls.clear()
        self.output.clear()

    def test_removes_exactly_the_allowlisted_files(self):
        keep = [self.root.file("usr/local/libexec/other-tool"),
                self.root.file("usr/local/share/polkit-1/actions/org.example.policy"),
                self.root.file("etc/systemd/system/other.service"),
                self.root.file("etc/systemd/system/" + SOCKET + ".d/admin.conf")]
        self.root.file("etc/systemd/system/" + SOCKET + ".d/owner.conf")
        wants = self.root.directory("etc/systemd/system/sockets.target.wants")/SOCKET
        wants.symlink_to("/etc/systemd/system/" + SOCKET)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "remove"}])
        self.assertEqual(self.installed(), sorted(str(path.relative_to(self.root.path))
                                                  for path in keep))
        self.assertFalse((self.root.path/"usr/local/libexec/system-device-monitor").exists())
        self.assertEqual(self.systemctl_calls(), [("disable", "--now", SOCKET), ("stop", SERVICE),
                                                  ("disable", LIMIT_UNIT), ("daemon-reload",)])

    def test_removes_the_charge_limit_links_and_its_own_state(self):
        links = [self.root.directory("etc/systemd/system/" + target + ".wants")/LIMIT_UNIT
                 for target in ("multi-user.target", "sleep.target")]
        for link in links:
            link.symlink_to("/etc/systemd/system/" + LIMIT_UNIT)
        self.root.file(LIMIT_STATE, "{}\n")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertFalse(any(os.path.lexists(link) for link in links))
        self.assertFalse((self.root.path/"var/lib/system-device-monitor").exists())
        self.assertTrue((self.root.path/"var/lib").is_dir())

    def test_removes_the_helper_lock(self):
        self.root.file("run/system-device-monitor/charge-limit.lock", "")
        self.root.file("run/system-device-monitor/charge-limit.times", "{}")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertFalse((self.root.path/"run/system-device-monitor").exists())
        self.assertTrue((self.root.path/"run").is_dir())

    def test_keeps_other_runtime_files(self):
        self.root.file("run/system-device-monitor/charge-limit.lock", "")
        other = self.root.file("run/system-device-monitor/cpu-power.sock", "")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertTrue(other.exists())

    def test_keeps_the_package_state_file(self):
        self.root.file(LIMIT_STATE, "{}\n")
        package = self.root.file("var/lib/system-device-monitor/charge-limit.json", "{}\n")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertFalse((self.root.path/LIMIT_STATE).exists())
        self.assertTrue(package.exists())

    def test_state_that_is_a_directory_is_left_alone(self):
        self.root.directory(LIMIT_STATE)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertTrue((self.root.path/LIMIT_STATE).is_dir())

    def test_keeps_the_helper_directory_when_it_holds_other_files(self):
        other = self.root.file("usr/local/libexec/system-device-monitor/notes.txt")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertTrue(other.exists())

    def test_removal_works_when_nothing_is_installed(self):
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)

    def test_removal_is_refused_while_the_package_is_installed(self):
        """套件與原始碼版共用無人授權的規則與喚醒紀錄：套件在時不移除任何東西。"""
        self.root.file(RULE, "// rule\n")
        alarm = self.wake("1800000600\n", "1800000600\n")
        before = self.installed()
        for status in ("install ok installed", "install ok unpacked", "hold ok installed"):
            with self.subTest(status=status):
                self.runner.package_status = status
                self.runner.calls.clear()
                self.assertEqual(self.main("remove"), installer.EXIT_FAILED)
                self.assertEqual(self.output[-1], {"ok": False, "error": "deb-installed"})
                self.assertEqual((self.installed(), self.systemctl_calls()), (before, []))
                self.assertEqual(alarm.read_text(), "1800000600\n")
        self.runner.package_status = "deinstall ok config-files"
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertFalse((self.root.path/RULE).exists())

    def wake(self, alarm, record, record_mode=0o600):
        """假的 RTC（rtc0 是指向 sys/devices 的符號連結）與 sdm-wake 記下的時間。"""
        path = self.root.file(ALARM, alarm)
        link = self.root.directory("sys/class/rtc")/"rtc0"
        link.symlink_to(os.path.relpath(path.parent, link.parent))
        self.root.file("run/system-device-monitor/wake", record, mode=record_mode)
        return path

    def test_clears_only_the_alarm_the_wake_helper_recorded(self):
        alarm = self.wake("1800000600\n", "1800000600\n")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(alarm.read_text(), "0")
        self.assertFalse((self.root.path/"run/system-device-monitor").exists())

    def test_leaves_another_alarm(self):
        alarm = self.wake("1800009999\n", "1800000600\n")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(alarm.read_text(), "1800009999\n")

    def test_record_writable_by_others_is_not_trusted(self):
        alarm = self.wake("1800000600\n", "1800000600\n", record_mode=0o666)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(alarm.read_text(), "1800000600\n")

    def test_removes_the_unattended_rule_and_the_helper_locks(self):
        rules = self.root.directory("etc/polkit-1/rules.d")
        os.chmod(rules, 0o750)
        self.root.file(RULE, "// rule\n")
        other = self.root.file("etc/polkit-1/rules.d/50-other.rules", "// other\n")
        for name in ("wake", "wake.lock", "wake.times", "unattended.lock"):
            self.root.file("run/system-device-monitor/" + name, "", mode=0o600)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertFalse((self.root.path/RULE).exists())
        self.assertTrue(other.exists())
        self.assertFalse((self.root.path/"run/system-device-monitor").exists())

    def hold_wake_lock(self):
        lock = self.root.file("run/system-device-monitor/wake.lock", "", mode=0o600)
        fd = os.open(lock, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_revokes_the_rule_then_waits_for_a_running_wake_helper(self):
        """先撤銷規則（不會再有免密碼的 sdm-wake 開始），等正在執行的 sdm-wake 放開鎖才取消鬧鐘。"""
        self.root.file(RULE, "// rule\n")
        alarm = self.wake("1800000600\n", "1800000600\n")
        fd = self.hold_wake_lock()
        seen = []

        def released_later():
            seen.append(((self.root.path/RULE).exists(), alarm.read_text()))
            if len(seen) == 3:
                fcntl.flock(fd, fcntl.LOCK_UN)
        self.on_sleep = released_later
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(seen, [(False, "1800000600\n")] * 3)
        self.assertEqual(alarm.read_text(), "0")
        self.assertFalse((self.root.path/"run/system-device-monitor").exists())

    def test_stuck_wake_lock_still_lets_removal_finish(self):
        alarm = self.wake("1800000600\n", "1800000600\n")
        self.hold_wake_lock()
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertGreaterEqual(sum(self.sleeps), installer.WAKE_LOCK_WAIT_S)
        self.assertLess(sum(self.sleeps), 2 * installer.WAKE_LOCK_WAIT_S + 1)   # 小程式刪除前後各等一次
        self.assertEqual(alarm.read_text(), "0")

    def test_untrusted_wake_lock_is_not_waited_for(self):
        alarm = self.wake("1800000600\n", "1800000600\n")
        target = self.root.file("run/elsewhere.lock", "", mode=0o600)
        (self.root.path/"run/system-device-monitor/wake.lock").symlink_to(target)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual((self.sleeps, alarm.read_text()), ([], "0"))
        self.assertTrue(target.exists())

    def test_waits_for_a_running_grant_before_revoking(self):
        """正在執行的 sdm-unattended grant 寫完規則之後才撤銷（同一把鎖，不建立、不跟隨）。"""
        self.root.file(RULE, "// rule\n")
        lock = self.root.file("run/system-device-monitor/unattended.lock", "", mode=0o600)
        fd = os.open(lock, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        seen = []

        def released_later():
            seen.append((self.root.path/RULE).exists())
            if len(seen) == 2:
                (self.root.path/RULE).write_text("// written by the running grant\n")
                fcntl.flock(fd, fcntl.LOCK_UN)
        self.on_sleep = released_later
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(seen, [True, True])
        self.assertFalse((self.root.path/RULE).exists())

    def test_cleans_again_after_the_helpers_are_gone(self):
        """移除檔案之前才完成的 sdm-wake set 或 grant（授權在規則刪除前就給了）：刪除小程式之後
        再拿鎖、再撤銷規則並取消鬧鐘，最後才刪除紀錄與鎖檔。"""
        self.root.file(RULE, "// rule\n")
        alarm = self.wake("1800000600\n", "1800000600\n")
        for name in ("wake.lock", "unattended.lock"):
            self.root.file("run/system-device-monitor/" + name, "", mode=0o600)

        def late_helper(argv):
            if list(argv[1:]) == ["disable", "--now", SOCKET]:     # 第一次清理之後
                self.assertEqual(alarm.read_text(), "0")
                alarm.write_text("1800009000\n")
                self.root.file("run/system-device-monitor/wake", "1800009000\n", mode=0o600)
                (self.root.path/RULE).write_text("// late grant\n")
        self.runner.on_call = late_helper
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(alarm.read_text(), "0")
        self.assertFalse((self.root.path/RULE).exists())
        self.assertFalse((self.root.path/"run/system-device-monitor").exists())

    def test_wants_entry_that_is_not_a_symlink_is_left_alone(self):
        entry = self.root.file("etc/systemd/system/sockets.target.wants/" + SOCKET)
        self.main("remove")
        self.assertTrue(entry.exists())


class Arguments(Base):
    def test_bad_arguments_exit_two(self):
        stage = str(self.stage)
        cases = ([], ["install"], ["upgrade", "--stage", stage, "--manifest-sha256", self.digest],
                 ["install", "--stage", "relative", "--manifest-sha256", self.digest],
                 ["install", "--stage", stage + "/../x", "--manifest-sha256", self.digest],
                 ["install", "--stage", stage + "/", "--manifest-sha256", self.digest],
                 ["install", "--stage", stage, "--manifest-sha256", self.digest.upper()],
                 ["install", "--stage", stage, "--manifest-sha256", self.digest[:-1]],
                 ["install", "--manifest-sha256", self.digest, "--stage", stage],
                 ["install", "--stage", stage, "--manifest-sha256", self.digest, "extra"])
        for argv in cases:
            with self.subTest(argv=argv):
                self.output.clear()
                self.assertEqual(self.main(argv=argv), installer.EXIT_USAGE)
                self.assertEqual(self.output, [{"ok": False, "error": "usage"}])
        self.assertEqual(self.runner.calls, [])

    def test_needs_root_through_pkexec(self):
        for options in ({"euid": 1000}, {"env": {}}, {"env": {"PKEXEC_UID": "0"}},
                        {"env": {"PKEXEC_UID": "x"}}):
            with self.subTest(options=options):
                self.output.clear()
                self.assertEqual(self.main(**options), installer.EXIT_NOT_PERMITTED)
                self.assertEqual(self.output, [{"ok": False, "error": "not-permitted"}])
        self.assertEqual(self.installed(), [])


class Consistency(unittest.TestCase):
    def test_targets_match_the_helper_layout(self):
        local = control.layout_for(CpuPower.LOCAL_HELPER)
        self.assertEqual((installer.SOCKET, installer.SERVICE), (local.socket, local.service))
        self.assertEqual({target: mode for _, target, mode in installer.FILES}, TARGETS)
        self.assertEqual("/" + installer.HELPER_DIR, CpuPower.LOCAL_HELPER_DIR)
        local_limit = limit.layout_for(ChargeLimit.LOCAL_HELPER)
        self.assertEqual(installer.LIMIT_UNIT, local_limit.unit)
        self.assertEqual("/" + installer.LIMIT_STATE, ChargeLimit.LOCAL_STATE)
        self.assertEqual(installer.LIMIT_STATE, limit.STATE_DIR + "/" + local_limit.state)
        self.assertEqual(installer.LIMIT_LOCK, limit.RUN_DIR + "/" + limit.LOCK_NAME)
        self.assertEqual(installer.LIMIT_TIMES, limit.RUN_DIR + "/" + limit.TIMES_NAME)
        self.assertEqual("/" + installer.HELPER_DIR + "/sdm-wake", Wake.LOCAL_HELPER)
        self.assertEqual("/" + installer.HELPER_DIR + "/sdm-unattended", Unattended.LOCAL_HELPER)
        self.assertEqual(installer.WAKE_FILES, tuple(wake.RUN_DIR + "/" + name for name in (
            wake.RECORD_NAME, wake.LOCK_NAME, wake.TIMES_NAME)))
        self.assertEqual(installer.WAKE_RECORD, wake.RUN_DIR + "/" + wake.RECORD_NAME)
        self.assertEqual(installer.RTC_ALARM, wake.RTC_DIR + "/" + wake.ALARM_FILE)
        self.assertEqual(installer.UNATTENDED_LOCK, unattended.RUN_DIR + "/" + unattended.LOCK_NAME)
        self.assertEqual("/" + installer.UNATTENDED_RULE, Unattended.RULE_FILE)

    def test_sources_exist_and_are_public(self):
        manifest = json.loads((ROOT/"package-files.json").read_text(encoding="utf-8"))
        for source, _, _ in installer.FILES:
            self.assertIn(source, manifest["source"])
            self.assertTrue((ROOT/source).is_file())

    def test_script_is_self_contained(self):
        text = (ROOT/"scripts/install_system.py").read_text(encoding="utf-8")
        self.assertNotIn("import sdm", text)
        self.assertNotIn("from sdm", text)
        self.assertNotIn("sys.path", text)


if __name__ == "__main__":
    unittest.main()
