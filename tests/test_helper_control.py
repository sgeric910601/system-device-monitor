"""CPU 功耗取樣的管理員小程式（helper/sdm-cpu-power）：參數、權限、drop-in 與 systemctl。

以假根目錄（擁有者＝執行測試的使用者）代替 /，systemctl 以記錄呼叫的假函式代替；
不需要 root，也不會碰到真正的 systemd。
"""
import os
import stat
import unittest

from sdm.constants import CpuPower
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import load_helper

control = load_helper("sdm-cpu-power")
DEB_SOCKET = "system-device-monitor-cpu-power.socket"
DEB_SERVICE = "system-device-monitor-cpu-power.service"
LOCAL_SOCKET = "system-device-monitor-local-cpu-power.socket"
LOCAL_SERVICE = "system-device-monitor-local-cpu-power.service"
DROPIN = "etc/systemd/system/" + DEB_SOCKET + ".d/owner.conf"
SYSTEMCTL = "/usr/bin/systemctl"
USER = 1000
ENV = {"PKEXEC_UID": str(USER)}


class Runner:
    """假的 systemctl：記錄每次呼叫；failing 中的子命令回傳 1。"""

    def __init__(self, failing=(), results=None):
        self.calls, self.failing, self.results = [], set(failing), results or {}

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        if argv[1] in self.results:
            return self.results[argv[1]]
        return 1 if argv[1] in self.failing else 0


class Base(unittest.TestCase):
    helper = CpuPower.DEB_HELPER

    def setUp(self):
        self.root = FakeRoot(self)
        self.runner = Runner()
        self.output, self.logged = [], []
        for name in (DEB_SOCKET, DEB_SERVICE):
            self.root.file("usr/lib/systemd/system/" + name)
        for name in (LOCAL_SOCKET, LOCAL_SERVICE):
            self.root.file("etc/systemd/system/" + name)
        for helper in (CpuPower.DEB_HELPER, CpuPower.LOCAL_HELPER):
            self.root.file(helper.lstrip("/"), mode=0o755)

    def context(self, euid=0, known=lambda uid: True, helper=None):
        return control.Context(root=str(self.root.path), owner=os.getuid(), run=self.runner,
                               euid=euid, user_exists=known, helper_path=helper or self.helper)

    def main(self, argv, env=ENV, **options):
        return control.main(argv, dict(env), self.context(**options), emit=self.output.append,
                            report=self.logged.append)


class Arguments(Base):
    def test_bad_arguments_exit_two(self):
        cases = ([], [""], ["bogus"], ["ENABLE"], ["--enable"], ["enable", "extra"],
                 ["status", "--protocol"], ["--protocol", "enable"], ["-h"], ["--help"],
                 ["enable;reboot"], ["disable", ""])
        for argv in cases:
            with self.subTest(argv=argv):
                self.output.clear()
                self.assertEqual(self.main(argv), control.EXIT_USAGE)
                self.assertEqual(self.output, [{"ok": False, "error": "usage"}])
        self.assertEqual(self.runner.calls, [])

    def test_bad_arguments_are_checked_before_permissions(self):
        self.assertEqual(self.main(["bogus"], env={}, euid=1000), control.EXIT_USAGE)

    def test_protocol_needs_no_privileges(self):
        self.assertEqual(self.main(["--protocol"], env={}, euid=1000), control.EXIT_OK)
        self.assertEqual(self.output, [{"protocol": CpuPower.PROTOCOL,
                                        "version": control.VERSION}])
        self.assertEqual(self.logged, [])


class Permissions(Base):
    def assert_refused(self, **options):
        self.output.clear()
        self.assertEqual(self.main(["enable"], **options), control.EXIT_NOT_PERMITTED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-permitted"}])

    def test_needs_root_and_pkexec(self):
        self.assert_refused(euid=1000)
        self.assert_refused(env={})
        for value in ("", "abc", "0", "-1", "1000 ", " 1000", "01000", "65534", "4294967295",
                      "99999999999", "1e3", "١٠٠٠"):
            with self.subTest(value=value):
                self.assert_refused(env={"PKEXEC_UID": value})
        self.assertEqual(self.runner.calls, [])

    def test_unknown_user_is_refused(self):
        self.assert_refused(known=lambda uid: False)

    def test_refusals_are_logged_without_details(self):
        self.main(["status"], env={})
        self.assertEqual(self.logged, ["status not-permitted"])


class Location(Base):
    def test_unknown_helper_location_fails(self):
        for helper in ("/tmp/sdm-cpu-power", "/usr/libexec/system-device-monitor/other",
                       "/usr/libexec/system-device-monitor/../x/sdm-cpu-power",
                       "usr/libexec/system-device-monitor/sdm-cpu-power"):
            with self.subTest(helper=helper):
                self.assertEqual(self.main(["status"], helper=helper), control.EXIT_FAILED)
        self.assertEqual(self.runner.calls, [])

    def test_layouts(self):
        deb = control.layout_for(CpuPower.DEB_HELPER)
        local = control.layout_for(CpuPower.LOCAL_HELPER)
        self.assertEqual((deb.kind, deb.socket, deb.service), ("deb", DEB_SOCKET, DEB_SERVICE))
        self.assertEqual((local.kind, local.socket, local.service),
                         ("local", LOCAL_SOCKET, LOCAL_SERVICE))
        self.assertEqual(deb.unit_dir, "usr/lib/systemd/system")
        self.assertEqual(local.unit_dir, "etc/systemd/system")

    def test_writable_helper_is_refused(self):
        os.chmod(self.root.path/CpuPower.DEB_HELPER.lstrip("/"), 0o775)
        self.assertEqual(self.main(["enable"]), control.EXIT_FAILED)
        self.assertEqual(self.runner.calls, [])


class Enable(Base):
    def test_writes_the_dropin_and_restarts_the_socket(self):
        self.assertEqual(self.main(["enable"]), control.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "enable"}])
        text = (self.root.path/DROPIN).read_text()
        self.assertIn("[Socket]\nSocketUser=1000\nSocketMode=0600\n", text)
        self.assertEqual(self.root.mode(DROPIN), 0o644)
        self.assertEqual(self.root.mode(os.path.dirname(DROPIN)), 0o755)
        self.assertEqual(self.runner.calls, [
            (SYSTEMCTL, "daemon-reload"), (SYSTEMCTL, "stop", DEB_SERVICE),
            (SYSTEMCTL, "enable", DEB_SOCKET), (SYSTEMCTL, "restart", DEB_SOCKET)])
        self.assertEqual(self.logged, ["enable ok"])
        leftovers = [name for name in os.listdir(self.root.path/os.path.dirname(DROPIN))
                     if name != "owner.conf"]
        self.assertEqual(leftovers, [])

    def test_local_layout_uses_its_own_units(self):
        self.helper = CpuPower.LOCAL_HELPER
        self.assertEqual(self.main(["enable"]), control.EXIT_OK)
        self.assertTrue((self.root.path/"etc/systemd/system"/(LOCAL_SOCKET + ".d")/
                         "owner.conf").is_file())
        self.assertIn((SYSTEMCTL, "restart", LOCAL_SOCKET), self.runner.calls)

    def test_replaces_an_older_dropin(self):
        self.root.file(DROPIN, "[Socket]\nSocketUser=1001\n")
        self.assertEqual(self.main(["enable"]), control.EXIT_OK)
        self.assertIn("SocketUser=1000", (self.root.path/DROPIN).read_text())

    def test_missing_units_fail_before_writing(self):
        os.unlink(self.root.path/"usr/lib/systemd/system"/DEB_SERVICE)
        self.assertEqual(self.main(["enable"]), control.EXIT_FAILED)
        self.assertFalse((self.root.path/os.path.dirname(DROPIN)).exists())
        self.assertEqual(self.runner.calls, [])

    def test_untrusted_directories_are_refused(self):
        for relative in ("etc", "etc/systemd/system", "usr/lib/systemd/system", "."):
            with self.subTest(relative=relative):
                path = self.root.path/relative
                os.chmod(path, 0o775)
                self.assertEqual(self.main(["enable"]), control.EXIT_FAILED)
                os.chmod(path, 0o755)
                self.assertFalse((self.root.path/DROPIN).exists())

    def test_symlinked_dropin_directory_is_refused(self):
        elsewhere = self.root.directory("elsewhere")
        os.symlink(elsewhere, self.root.path/"etc/systemd/system"/(DEB_SOCKET + ".d"))
        self.assertEqual(self.main(["enable"]), control.EXIT_FAILED)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_symlinked_dropin_file_is_refused(self):
        target = self.root.file("elsewhere/secret", "keep\n")
        self.root.directory("etc/systemd/system/" + DEB_SOCKET + ".d")
        os.symlink(target, self.root.path/DROPIN)
        self.assertEqual(self.main(["enable"]), control.EXIT_FAILED)
        self.assertEqual(target.read_text(), "keep\n")
        self.assertTrue(os.path.islink(self.root.path/DROPIN))

    def test_failed_systemctl_stops_and_fails(self):
        self.runner.failing = {"enable"}
        self.assertEqual(self.main(["enable"]), control.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "failed"}])
        self.assertNotIn((SYSTEMCTL, "restart", DEB_SOCKET), self.runner.calls)
        self.assertEqual(self.logged, ["enable failed"])


class Disable(Base):
    def test_stops_units_and_removes_the_dropin(self):
        self.root.file(DROPIN, "[Socket]\nSocketUser=1000\n")
        self.assertEqual(self.main(["disable"]), control.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "disable"}])
        self.assertFalse((self.root.path/os.path.dirname(DROPIN)).exists())
        self.assertEqual(self.runner.calls, [
            (SYSTEMCTL, "disable", "--now", DEB_SOCKET), (SYSTEMCTL, "stop", DEB_SERVICE),
            (SYSTEMCTL, "daemon-reload")])

    def test_other_dropins_are_kept(self):
        self.root.file(DROPIN, "[Socket]\nSocketUser=1000\n")
        other = self.root.file(os.path.dirname(DROPIN) + "/admin.conf", "[Socket]\n")
        self.assertEqual(self.main(["disable"]), control.EXIT_OK)
        self.assertFalse((self.root.path/DROPIN).exists())
        self.assertTrue(other.exists())

    def test_disable_without_a_dropin_succeeds(self):
        self.assertEqual(self.main(["disable"]), control.EXIT_OK)

    def test_failure_still_removes_the_dropin(self):
        self.root.file(DROPIN, "[Socket]\nSocketUser=1000\n")
        self.runner.failing = {"disable"}
        self.assertEqual(self.main(["disable"]), control.EXIT_FAILED)
        self.assertFalse((self.root.path/DROPIN).exists())
        self.assertIn((SYSTEMCTL, "daemon-reload"), self.runner.calls)

    def test_unexpected_dropin_type_is_not_removed(self):
        self.root.directory(DROPIN)
        self.assertEqual(self.main(["disable"]), control.EXIT_FAILED)
        self.assertTrue((self.root.path/DROPIN).is_dir())


class Status(Base):
    def test_reports_systemd_state_and_owner(self):
        self.root.file(DROPIN, control.dropin_text(1234))
        self.assertEqual(self.main(["status"]), control.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "status", "enabled": True,
                                        "active": True, "owner_uid": 1234}])
        self.assertEqual(self.runner.calls, [
            (SYSTEMCTL, "is-enabled", "--quiet", DEB_SOCKET),
            (SYSTEMCTL, "is-active", "--quiet", DEB_SOCKET)])

    def test_disabled_without_dropin(self):
        self.runner.results = {"is-enabled": 1, "is-active": 3}
        self.assertEqual(self.main(["status"]), control.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "status", "enabled": False,
                                        "active": False, "owner_uid": None}])

    def test_malformed_dropin_owner_is_unknown(self):
        self.root.file(DROPIN, "[Socket]\nSocketUser=root;x\n")
        self.main(["status"])
        self.assertIsNone(self.output[0]["owner_uid"])


class Commands(unittest.TestCase):
    def test_systemctl_runs_without_a_shell_and_with_a_fixed_path(self):
        seen = {}

        def fake_run(argv, **options):
            seen.update(options, argv=argv)

            class Result:
                returncode = 5
            return Result()

        original = control.subprocess.run
        control.subprocess.run = fake_run
        try:
            self.assertEqual(control.run_command([SYSTEMCTL, "status"]), 5)
        finally:
            control.subprocess.run = original
        self.assertEqual(seen["argv"], [SYSTEMCTL, "status"])
        self.assertEqual(seen["env"], {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
        self.assertNotIn("shell", seen)
        self.assertEqual(seen["timeout"], control.COMMAND_TIMEOUT_S)

    def test_missing_program_is_a_failure(self):
        self.assertNotEqual(control.run_command(["/nonexistent/systemctl"]), 0)


class Helper(unittest.TestCase):
    def test_runs_isolated_and_is_executable(self):
        path = control.__file__
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.readline().rstrip("\n"), "#!/usr/bin/python3 -IB")
        mode = os.stat(path).st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertFalse(mode & (stat.S_IWGRP | stat.S_IWOTH))

    def test_constants_match_the_application(self):
        self.assertEqual(control.PROTOCOL, CpuPower.PROTOCOL)
        self.assertEqual((control.EXIT_OK, control.EXIT_USAGE, control.EXIT_FAILED,
                          control.EXIT_NOT_PERMITTED), CpuPower.EXIT_CODES)
        self.assertEqual([layout.helper_dir for layout in control.LAYOUTS],
                         [CpuPower.DEB_HELPER_DIR, CpuPower.LOCAL_HELPER_DIR])
        self.assertEqual(control.HELPER_NAME, os.path.basename(CpuPower.DEB_HELPER))


if __name__ == "__main__":
    unittest.main()
