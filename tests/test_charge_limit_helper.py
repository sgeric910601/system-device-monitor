"""充電上限小程式（helper/sdm-charge-limit）：參數驗證表、權限、執行路徑、電池檢查與常數。

以假根目錄（擁有者＝執行測試的使用者）代替 /，驅動與 systemctl 以假函式代替；
不需要 root，也不會碰到真正的 sysfs 或 systemd。寫入順序、讀回、保留與重新套用在
test_charge_limit_writes。
"""
import os
import stat
import unittest

from sdm.constants import ChargeLimit, CpuPower
from tests.fixtures.charge_root import DEB_UNIT, ENV, LOCAL_UNIT, Base, helper
from tests.fixtures.helpers import git_mode

BAD_LIMITS = ("49", "101", "-80", "79.5", "8e1", " 80", "080", "", "1e2", "+80", "80 ", "80\n",
              "８０", "٨٠", "0x50", "50.0", "1000", "0", "5")
GOOD_LIMITS = ("50", "55", "79", "80", "99", "100")
BAD_SUPPLIES = ("BAT0/../x", "BAT0 ", "", " BAT0", "BAT0/", "../BAT0", "BAT-0", "BAT.0", "A" * 33,
                "BAT0\n", "БАТ0", "BAT٠", "BAT0\0")


class Arguments(Base):
    def assert_usage(self, argv):
        self.output.clear()
        self.assertEqual(self.main(argv), helper.EXIT_USAGE)
        self.assertEqual(self.output, [{"ok": False, "error": "usage"}])

    def test_bad_commands_exit_two(self):
        for argv in ([], [""], ["bogus"], ["SET", "BAT0", "80"], ["set"], ["set", "BAT0"],
                     ["status", "extra"], ["restore"], ["restore", "--from-unit", "x"],
                     ["restore", "--from-units"], ["--protocol", "extra"], ["-h"], ["--help"],
                     ["set", "BAT0", "80", "extra"], ["set;reboot", "BAT0", "80"]):
            with self.subTest(argv=argv):
                self.assert_usage(argv)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.driver.writes, [])

    def test_bad_limits_exit_two(self):
        self.battery()
        for value in BAD_LIMITS:
            with self.subTest(value=value):
                self.assert_usage(["set", "BAT0", value])
        self.assertEqual(self.driver.writes, [])

    def test_good_limits_are_accepted(self):
        self.battery(start=None)
        for value in GOOD_LIMITS:
            with self.subTest(value=value):
                self.output.clear()
                self.assertEqual(self.main(["set", "BAT0", value]), helper.EXIT_OK)
                self.assertEqual(self.output[0]["end"], int(value))

    def test_bad_supply_names_exit_two(self):
        for name in BAD_SUPPLIES:
            with self.subTest(name=name):
                self.assert_usage(["set", name, "80"])

    def test_start_is_never_accepted(self):
        """起點只由小程式在驅動需要時自動計算，呼叫者不能指定。"""
        self.battery()
        for options in (["--start", "0"], ["--start", "70"], ["--start", "80"], ["--start", "90"],
                        ["--start", "-1"], ["--start", ""], ["--start"],
                        ["--persist", "off", "--start", "70"]):
            with self.subTest(options=options):
                self.assert_usage(["set", "BAT0", "80", *options])
        self.assertEqual(self.driver.writes, [])

    def test_bad_persist_options_exit_two(self):
        for options in (["--persist", "maybe"], ["--persist", "ON"], ["--persist"],
                        ["--persist", "on", "--persist", "off"], ["--force", "1"],
                        ["--persist=on"], ["on"]):
            with self.subTest(options=options):
                self.assert_usage(["set", "BAT0", "80", *options])

    def test_persist_option_is_accepted(self):
        self.battery()
        self.assertEqual(self.main(["set", "BAT0", "80", "--persist", "off"]), helper.EXIT_OK)

    def test_bad_arguments_are_checked_before_permissions(self):
        self.assertEqual(self.main(["set", "BAT0", "101"], env={}, euid=1000), helper.EXIT_USAGE)

    def test_protocol_needs_no_privileges(self):
        self.assertEqual(self.main(["--protocol"], env={}, euid=1000), helper.EXIT_OK)
        self.assertEqual(self.output, [{"protocol": ChargeLimit.PROTOCOL,
                                        "version": helper.VERSION}])
        self.assertEqual(self.logged, [])


class Permissions(Base):
    def setUp(self):
        super().setUp()
        self.battery()

    def assert_refused(self, argv=("set", "BAT0", "85"), **options):
        self.output.clear()
        self.assertEqual(self.main(list(argv), **options), helper.EXIT_NOT_PERMITTED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-permitted"}])
        self.assertEqual(self.driver.writes, [])

    def test_set_needs_root_and_pkexec(self):
        self.assert_refused(euid=1000)
        self.assert_refused(env={})
        for value in ("", "abc", "0", "-1", "1000 ", " 1000", "01000", "65534", "4294967295",
                      "99999999999", "1e3", "١٠٠٠"):
            with self.subTest(value=value):
                self.assert_refused(env={"PKEXEC_UID": value})
        self.assertEqual(self.runner.calls, [])

    def test_uid_must_be_a_regular_login_account(self):
        for value in ("999", "60001", "65000", "1"):
            with self.subTest(value=value):
                self.assert_refused(env={"PKEXEC_UID": value})
        self.assert_refused(known=lambda uid: False)
        self.assertEqual(self.main(["status"], env={"PKEXEC_UID": "600"}, uid_range=(500, 2000)),
                         helper.EXIT_OK)

    def test_status_needs_pkexec_too(self):
        self.assert_refused(("status",), env={})

    def test_restore_is_refused_through_pkexec(self):
        env = dict(ENV, INVOCATION_ID="0123456789abcdef0123456789abcdef")
        self.assert_refused(("restore", "--from-unit"), env=env, ppid=1)

    def test_restore_needs_the_unit_environment(self):
        argv = ("restore", "--from-unit")
        good = {"INVOCATION_ID": "0123456789abcdef0123456789abcdef"}
        self.assert_refused(argv, env={}, ppid=1)
        self.assert_refused(argv, env=good, ppid=4321)
        self.assert_refused(argv, env=good, ppid=1, euid=1000)
        for value in ("", "x" * 32, "0123456789ABCDEF0123456789ABCDEF", "0" * 31, "0" * 33):
            with self.subTest(value=value):
                self.assert_refused(argv, env={"INVOCATION_ID": value}, ppid=1)

    def test_set_is_refused_from_the_unit(self):
        env = {"INVOCATION_ID": "0123456789abcdef0123456789abcdef"}
        self.assert_refused(env=env, ppid=1)

    def test_refusals_are_logged_without_details(self):
        self.main(["set", "BAT0", "85"], env={})
        self.assertEqual(self.logged, ["set not-permitted"])


class Location(Base):
    def setUp(self):
        super().setUp()
        self.battery()

    def test_unknown_helper_location_fails(self):
        for path in ("/tmp/sdm-charge-limit", "/usr/libexec/system-device-monitor/other",
                     "/usr/libexec/system-device-monitor/../x/sdm-charge-limit",
                     "usr/libexec/system-device-monitor/sdm-charge-limit",
                     "/usr/libexec//system-device-monitor/sdm-charge-limit",
                     "//usr/libexec/system-device-monitor/sdm-charge-limit",
                     "/usr/libexec/system-device-monitor/./sdm-charge-limit",
                     "/usr/libexec/system-device-monitor/sdm-charge-limit/",
                     CpuPower.DEB_HELPER, "./sdm-charge-limit", "sdm-charge-limit"):
            with self.subTest(path=path):
                self.output.clear()
                self.assertEqual(self.main(["set", "BAT0", "85"], helper=path), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "unknown-location"}])
        self.assertEqual(self.driver.writes, [])

    def test_production_context_keeps_the_path_exactly_as_executed(self):
        original = helper.sys.argv
        odd = "/usr/libexec/system-device-monitor/./sdm-charge-limit"
        helper.sys.argv = [odd, "status"]
        try:
            context = helper.production_context()
        finally:
            helper.sys.argv = original
        self.assertEqual(context.helper_path, odd)
        self.assertEqual(context.ppid, os.getppid())
        self.assertGreater(context.boottime(), 0)
        self.assertIs(context.write, os.write)

    def test_layouts(self):
        deb = helper.layout_for(ChargeLimit.DEB_HELPER)
        local = helper.layout_for(ChargeLimit.LOCAL_HELPER)
        self.assertEqual((deb.kind, deb.unit, deb.unit_dir), ("deb", DEB_UNIT,
                                                              "usr/lib/systemd/system"))
        self.assertEqual((local.kind, local.unit, local.unit_dir), ("local", LOCAL_UNIT,
                                                                    "etc/systemd/system"))
        self.assertEqual("/" + helper.STATE_DIR + "/" + deb.state, ChargeLimit.DEB_STATE)
        self.assertEqual("/" + helper.STATE_DIR + "/" + local.state, ChargeLimit.LOCAL_STATE)

    def test_writable_helper_or_unit_is_refused(self):
        for relative in (ChargeLimit.DEB_HELPER.lstrip("/"), "usr/lib/systemd/system/" + DEB_UNIT,
                         "usr/lib/systemd/system", "usr"):
            with self.subTest(relative=relative):
                path = self.root.path/relative
                mode = stat.S_IMODE(path.stat().st_mode)
                os.chmod(path, mode | 0o020)
                self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_FAILED)
                os.chmod(path, mode)
        self.assertEqual(self.driver.writes, [])

    def test_missing_unit_fails_before_writing(self):
        os.unlink(self.root.path/"usr/lib/systemd/system"/DEB_UNIT)
        self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_FAILED)
        self.assertEqual(self.driver.writes, [])


class Supplies(Base):
    def test_system_batteries_are_accepted(self):
        for name in ("BAT0", "BATT", "CMB0", "bat1"):
            self.battery(name, device="platform/" + name)
            with self.subTest(name=name):
                self.output.clear()
                self.assertEqual(self.main(["set", name, "85"]), helper.EXIT_OK)
                self.assertEqual(self.value(name, "charge_control_end_threshold",
                                            device="platform/" + name), 85)

    def assert_refused(self, name, error):
        self.output.clear()
        self.assertEqual(self.main(["set", name, "85"]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": error}])

    def test_missing_battery(self):
        self.battery("BAT0")
        for name in ("BAT", "BAT1", "bat0"):
            with self.subTest(name=name):
                self.assert_refused(name, "not-a-battery")
        self.assertEqual(self.driver.writes, [])

    def test_peripherals_and_adapters_are_refused(self):
        self.battery("hidpp_battery_0", scope="Device", device="usb/hidpp")
        self.battery("AC", kind="Mains", device="platform/AC")
        self.battery("UPS", kind="UPS", device="usb/ups")
        for name in ("hidpp_battery_0", "AC", "UPS"):
            with self.subTest(name=name):
                self.assert_refused(name, "not-a-battery")
        self.assertEqual(self.driver.writes, [])

    def test_battery_with_system_scope_is_accepted(self):
        self.battery("BAT0", scope="System")
        self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_OK)

    def test_battery_without_thresholds_is_unsupported(self):
        self.battery("CMB0", end=None, start=None)
        self.assert_refused("CMB0", "unsupported")

    def test_symlink_escaping_sys_devices_is_refused(self):
        outside = self.root.directory("elsewhere/power_supply/EVIL")
        for attribute, value in (("type", "Battery"), ("charge_control_end_threshold", "80")):
            (outside/attribute).write_text(value + "\n")
        link = self.root.path/"sys/class/power_supply/EVIL"
        link.symlink_to(os.path.relpath(outside, link.parent))
        self.assert_refused("EVIL", "not-a-battery")
        self.assertEqual((outside/"charge_control_end_threshold").read_text(), "80\n")

    def test_symlink_to_sys_devices_itself_is_refused(self):
        link = self.root.path/"sys/class/power_supply/TOP"
        self.root.directory("sys/devices")
        link.symlink_to("../../devices")
        self.assert_refused("TOP", "not-a-battery")

    def test_threshold_file_must_be_a_regular_file(self):
        directory = self.battery("BAT0", end=None)
        target = self.root.file("elsewhere/end", "80\n")
        (directory/"charge_control_end_threshold").symlink_to(target)
        self.assert_refused("BAT0", "unsupported")
        self.assertEqual(target.read_text(), "80\n")

    def test_type_file_that_is_a_symlink_is_not_trusted(self):
        directory = self.battery("BAT0", kind=None)
        (directory/"type").symlink_to(self.root.file("elsewhere/type", "Battery\n"))
        self.assert_refused("BAT0", "not-a-battery")


class Helper(unittest.TestCase):
    path = helper.__file__

    def test_runs_isolated_and_is_executable(self):
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(handle.readline().rstrip("\n"), "#!/usr/bin/python3 -IB")
        self.assertTrue(os.stat(self.path).st_mode & stat.S_IXUSR)
        self.assertIn(git_mode(self.path), (None, "100755"))

    def test_constants_match_the_application(self):
        self.assertEqual(helper.PROTOCOL, ChargeLimit.PROTOCOL)
        self.assertEqual((helper.EXIT_OK, helper.EXIT_USAGE, helper.EXIT_FAILED,
                          helper.EXIT_NOT_PERMITTED), ChargeLimit.EXIT_CODES)
        self.assertEqual([layout.helper_dir + "/" + helper.HELPER_NAME
                          for layout in helper.LAYOUTS],
                         [ChargeLimit.DEB_HELPER, ChargeLimit.LOCAL_HELPER])
        self.assertEqual([layout.unit for layout in helper.LAYOUTS],
                         [ChargeLimit.DEB_UNIT, ChargeLimit.LOCAL_UNIT])
        self.assertEqual(helper.SUPPLY_PATTERN.pattern, ChargeLimit.SUPPLY_PATTERN)
        self.assertEqual((helper.START_GAP, helper.READBACK_ATTEMPTS, helper.READBACK_INTERVAL_S,
                          helper.STATE_FORMAT, helper.STATE_MAX_BYTES),
                         (ChargeLimit.START_GAP, ChargeLimit.READBACK_ATTEMPTS,
                          ChargeLimit.READBACK_INTERVAL, ChargeLimit.STATE_FORMAT,
                          ChargeLimit.STATE_MAX_BYTES))
        self.assertEqual("/" + helper.STATE_DIR, ChargeLimit.STATE_DIR)
        self.assertEqual(helper.SYSTEMCTL, ChargeLimit.SYSTEMCTL)
        self.assertEqual(("/" + helper.RUN_DIR, helper.LOCK_NAME, helper.LOCK_WAIT_S,
                          helper.LOCK_POLL_S),
                         (ChargeLimit.RUN_DIR, ChargeLimit.LOCK_NAME, ChargeLimit.LOCK_WAIT,
                          ChargeLimit.LOCK_POLL))
        self.assertEqual((helper.TIMES_NAME, helper.MIN_WRITE_GAP_S),
                         (ChargeLimit.TIMES_NAME, ChargeLimit.MIN_WRITE_GAP))

    def test_systemctl_runs_without_a_shell_and_with_a_fixed_path(self):
        seen = {}

        def fake_run(argv, **options):
            seen.update(options, argv=argv)

            class Result:
                returncode = 5
            return Result()

        original = helper.subprocess.run
        helper.subprocess.run = fake_run
        try:
            self.assertEqual(helper.run_command(["/usr/bin/systemctl", "status"]), 5)
        finally:
            helper.subprocess.run = original
        self.assertEqual(seen["env"], {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
        self.assertNotIn("shell", seen)
        self.assertEqual(seen["timeout"], helper.COMMAND_TIMEOUT_S)

    def test_unknown_runner_failure_is_not_success(self):
        self.assertNotEqual(helper.run_command(["/nonexistent/systemctl"]), 0)


class Logging(Base):
    def test_log_lines_hold_only_the_action_and_outcome(self):
        self.battery()
        self.main(["set", "BAT0", "85"])
        self.main(["set", "BAT9", "85"])
        self.main(["set", "BAT0", "101"])
        self.assertEqual(self.logged, ["set ok", "set failed"])
        for line in self.logged:
            self.assertNotIn("BAT", line)
            self.assertNotIn("85", line)


if __name__ == "__main__":
    unittest.main()
