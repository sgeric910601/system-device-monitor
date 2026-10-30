"""定時喚醒小程式（helper/sdm-wake）：參數驗證表、權限、執行路徑、RTC 檢查、
排定與取消的規則、一次只執行一個、寫入間隔與常數。

以假根目錄（擁有者＝執行測試的使用者）代替 /，RTC 驅動以假函式代替；
不需要 root，也不會碰到真正的 /sys/class/rtc。
"""
import fcntl
import os
import signal
import stat
import unittest

from sdm.constants import ChargeLimit, CpuPower, SmartCharging, Wake
from tests.fixtures.helpers import ROOT, git_mode
from tests.fixtures.wake_root import (ENV, LOCK, NOW, RECORD, RTC_DEVICE, TIMES, Base, Rtc,
                                      helper)

WEEK = 7 * 24 * 3600
BAD_EPOCHS = ("", "0", "-1", "+1800000600", " 1800000600", "1800000600 ", "1800000600\n",
              "1e9", "1800000600.5", "0x6b49d200", "01800000600", "１８００００００６００",
              "١٨٠٠٠٠٠٠٠٠", "9" * 13, "18OOOOO6OO", "1800000600\0")


class Arguments(Base):
    def assert_usage(self, argv):
        self.output.clear()
        self.assertEqual(self.main(argv), helper.EXIT_USAGE)
        self.assertEqual(self.output, [{"ok": False, "error": "usage"}])

    def test_bad_commands_exit_two(self):
        for argv in ([], [""], ["bogus"], ["SET", str(NOW + 600)], ["set"],
                     ["set", str(NOW + 600), "extra"], ["clear", "now"], ["status", "x"],
                     ["--protocol", "x"], ["-h"], ["--help"], ["set;reboot", "1"],
                     ["set", "--", str(NOW + 600)]):
            with self.subTest(argv=argv):
                self.assert_usage(argv)
        self.assertEqual(self.rtc.writes, [])

    def test_epoch_must_be_plain_ascii_digits(self):
        for value in BAD_EPOCHS:
            with self.subTest(value=value):
                self.assert_usage(["set", value])
        self.assertEqual(self.rtc.writes, [])

    def test_bad_arguments_are_checked_before_permissions(self):
        self.assertEqual(self.main(["set", "x"], env={}, euid=1000), helper.EXIT_USAGE)

    def test_protocol_needs_no_privileges(self):
        self.assertEqual(self.main(["--protocol"], env={}, euid=1000), helper.EXIT_OK)
        self.assertEqual(self.output, [{"protocol": Wake.PROTOCOL, "version": helper.VERSION}])
        self.assertEqual(self.logged, [])


class Window(Base):
    def assert_out_of_range(self, epoch):
        self.output.clear()
        self.assertEqual(self.main(["set", str(epoch)]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "out-of-range"}])

    def test_sixty_seconds_to_seven_days_ahead(self):
        for epoch in (NOW - 3600, NOW, NOW + 59, NOW + WEEK + 1, 10 ** 11):
            with self.subTest(epoch=epoch):
                self.assert_out_of_range(epoch)
        self.assertEqual(self.rtc.writes, [])
        for epoch in (NOW + 60, NOW + WEEK):
            with self.subTest(epoch=epoch):
                self.set_alarm("")
                self.boot += 100
                self.assertEqual(self.main(["set", str(epoch)]), helper.EXIT_OK)
                self.assertEqual(self.alarm(), str(epoch))


class Permissions(Base):
    def assert_refused(self, argv=("set", str(NOW + 600)), **options):
        self.output.clear()
        self.assertEqual(self.main(argv, **options), helper.EXIT_NOT_PERMITTED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-permitted"}])

    def test_every_command_needs_root_through_pkexec(self):
        for argv in (("set", str(NOW + 600)), ("clear",), ("status",)):
            with self.subTest(argv=argv):
                self.assert_refused(argv, euid=1000)
                self.assert_refused(argv, env={})
        for value in ("", "abc", "0", "-1", "1000 ", "01000", "65534", "4294967295", "999",
                      "60001", "1e3", "١٠٠٠"):
            with self.subTest(value=value):
                self.assert_refused(env={"PKEXEC_UID": value})
        self.assert_refused(known=lambda uid: False)
        self.assertEqual(self.rtc.writes, [])
        self.assertFalse((self.root.path/LOCK).exists())

    def test_other_login_ranges(self):
        self.assertEqual(self.main(["status"], env={"PKEXEC_UID": "600"}, uid_range=(500, 2000)),
                         helper.EXIT_OK)


class Location(Base):
    def test_exact_install_paths_only(self):
        for path in ("/usr/bin/sdm-wake", "//usr/libexec/system-device-monitor/sdm-wake",
                     "/usr/libexec/system-device-monitor/./sdm-wake", "sdm-wake",
                     "/usr/libexec/system-device-monitor/sdm-charge-limit"):
            with self.subTest(path=path):
                self.output.clear()
                self.assertEqual(self.main(["status"], helper_path=path), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "unknown-location"}])

    def test_local_install_works(self):
        self.assertEqual(self.main(["status"], helper_path=Wake.LOCAL_HELPER), helper.EXIT_OK)

    def test_writable_helper_or_parent_is_refused(self):
        helper_file = self.root.path/Wake.DEB_HELPER.lstrip("/")
        for path in (helper_file, helper_file.parent, self.root.path/"usr"):
            with self.subTest(path=path):
                original = stat.S_IMODE(path.stat().st_mode)
                path.chmod(original | stat.S_IWOTH)
                self.output.clear()
                self.assertEqual(self.main(["status"]), helper.EXIT_FAILED)
                path.chmod(original)
        helper_file.unlink()
        helper_file.symlink_to(self.root.path/Wake.LOCAL_HELPER.lstrip("/"))
        self.assertEqual(self.main(["status"]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "untrusted-file"})


class RtcChecks(Base):
    def assert_failed(self, error, argv=("set", str(NOW + 600))):
        self.output.clear()
        self.assertEqual(self.main(argv), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["error"], error)

    def test_missing_rtc(self):
        (self.root.path/"sys/class/rtc/rtc0").unlink()
        self.assert_failed("no-rtc")
        self.assert_failed("no-rtc", ("clear",))

    def test_rtc_must_resolve_under_sys_devices(self):
        link = self.root.path/"sys/class/rtc/rtc0"
        outside = self.root.directory("run/fake-rtc")
        (outside/"wakealarm").write_text("")
        (outside/"since_epoch").write_text(f"{NOW}\n")
        for target in (outside, self.root.path/"sys/devices", self.root.path):
            with self.subTest(target=target):
                link.unlink()
                link.symlink_to(target)
                self.assert_failed("no-rtc")
        self.assertEqual(self.rtc.writes, [])

    def test_wakealarm_must_be_a_regular_file(self):
        alarm = self.root.path/RTC_DEVICE/"wakealarm"
        alarm.unlink()
        self.assert_failed("no-rtc")
        target = self.root.file("run/elsewhere", "")
        alarm.symlink_to(target)
        self.assert_failed("no-rtc")
        self.assertEqual(target.read_text(), "")

    def test_rtc_kept_in_local_time_is_refused(self):
        """硬體時鐘不是 UTC（與 Windows 雙系統）時喚醒會差好幾個小時：拒絕。"""
        for since in (NOW + 8 * 3600, NOW - 301, "garbage"):
            with self.subTest(since=since):
                self.make_rtc(since=since)
                self.assert_failed("rtc-offset")
        self.make_rtc(since=NOW + 299)
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_OK)


class SetAndClear(Base):
    def test_set_writes_zero_then_the_time_and_records_it(self):
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "set", "alarm": NOW + 600}])
        self.assertEqual(self.rtc.writes, ["0", str(NOW + 600)])
        self.assertEqual(self.alarm(), str(NOW + 600))
        record = self.root.path/RECORD
        self.assertEqual(record.read_text(), f"{NOW + 600}\n")
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertEqual(self.logged, ["set ok"])

    def test_record_and_lock_are_private_whatever_the_umask(self):
        previous = os.umask(0)
        try:
            self.main(["set", str(NOW + 600)])
        finally:
            os.umask(previous)
        for name in (RECORD, LOCK, TIMES):
            with self.subTest(name=name):
                self.assertEqual(stat.S_IMODE((self.root.path/name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root.path/"run/system-device-monitor").stat().st_mode),
                         0o755)

    def test_someone_elses_alarm_is_never_replaced(self):
        self.set_alarm(NOW + 5000)
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "alarm-in-use"}])
        self.record(NOW + 1234)
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
        self.assertEqual((self.rtc.writes, self.alarm()), ([], str(NOW + 5000)))

    def test_own_alarm_is_replaced(self):
        self.set_alarm(NOW + 5000)
        self.record(NOW + 5000)
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_OK)
        self.assertEqual(self.alarm(), str(NOW + 600))
        self.assertEqual((self.root.path/RECORD).read_text(), f"{NOW + 600}\n")

    def test_alarm_that_already_fired_leaves_a_stale_record(self):
        self.record(NOW - 100)
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_OK)

    def test_different_value_after_writing_is_undone(self):
        self.rtc.rounding = 30
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-applied"}])
        self.assertEqual(self.alarm(), "")
        self.assertFalse((self.root.path/RECORD).exists())

    def test_driver_refusal(self):
        self.rtc.refuse = True
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "failed"}])
        self.assertFalse((self.root.path/RECORD).exists())
        self.assertEqual(self.logged, ["set failed"])

    def test_clear_only_our_own_alarm(self):
        self.assertEqual(self.main(["clear"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "clear", "cleared": False})
        self.assertEqual(self.rtc.writes, [])
        self.boot += 100
        self.main(["set", str(NOW + 600)])
        self.rtc.writes.clear()
        self.assertEqual(self.main(["clear"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "clear", "cleared": True})
        self.assertEqual((self.rtc.writes, self.alarm()), (["0"], ""))
        self.assertFalse((self.root.path/RECORD).exists())

    def test_clear_leaves_another_alarm_and_drops_the_stale_record(self):
        self.record(NOW + 600)
        self.set_alarm(NOW + 7000)
        self.assertEqual(self.main(["clear"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "clear", "cleared": False})
        self.assertEqual((self.rtc.writes, self.alarm()), ([], str(NOW + 7000)))
        self.assertFalse((self.root.path/RECORD).exists())

    def test_status(self):
        self.record(NOW + 600)
        self.set_alarm(NOW + 600)
        self.assertEqual(self.main(["status"]), helper.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "status", "rtc": True,
                                        "alarm": NOW + 600, "recorded": NOW + 600}])
        self.assertEqual(self.rtc.writes, [])

    def test_status_without_an_rtc(self):
        (self.root.path/"sys/class/rtc/rtc0").unlink()
        self.assertEqual(self.main(["status"]), helper.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "status", "rtc": False,
                                        "alarm": None, "recorded": None}])

    def test_untrusted_record_is_refused(self):
        record = self.record(NOW + 600)
        record.chmod(0o644)
        self.set_alarm(NOW + 600)
        self.assertEqual(self.main(["clear"]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["error"], "untrusted-file")
        record.unlink()
        record.symlink_to(self.root.file("run/elsewhere", f"{NOW + 600}\n", mode=0o600))
        self.assertEqual(self.main(["clear"]), helper.EXIT_FAILED)
        self.assertEqual(self.alarm(), str(NOW + 600))

    def test_garbage_record_counts_as_none(self):
        self.root.file(RECORD, "not a number\n", mode=0o600)
        self.set_alarm(NOW + 600)
        self.assertEqual(self.main(["set", str(NOW + 900)]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["error"], "alarm-in-use")


class Interruptions(Base):
    """RTC 上的鬧鐘一定已經記下（先取消舊的、記下新的時間，再寫入）；寫入時忽略終端機的訊號；
    任何例外都回復，結果仍是一行 JSON。"""

    def interrupt_after(self, count, error):
        """第 count 次寫入 RTC 之前拋出一次 error。"""
        raised = []

        def hook(_text):
            if len(self.rtc.writes) == count and not raised:
                raised.append(error)
                raise error
        self.rtc.hook = hook

    def record_text(self):
        path = self.root.path/RECORD
        return path.read_text() if path.exists() else None

    def test_alarm_on_the_rtc_is_always_recorded(self):
        self.record(NOW + 5000)
        self.set_alarm(NOW + 5000)
        seen = []
        self.rtc.hook = lambda text: seen.append((text, self.alarm(), self.record_text()))
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_OK)
        self.assertEqual(seen, [("0", str(NOW + 5000), f"{NOW + 5000}\n"),
                                (str(NOW + 600), "", f"{NOW + 600}\n")])

    def test_terminal_signals_are_ignored_while_writing(self):
        before = {number: signal.getsignal(number) for number in helper.TERMINAL_SIGNALS}
        seen = []
        self.rtc.hook = lambda _text: seen.append({signal.getsignal(number)
                                                   for number in helper.TERMINAL_SIGNALS})
        self.main(["set", str(NOW + 600)])
        self.boot += 100
        self.main(["clear"])
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(handlers == {signal.SIG_IGN} for handlers in seen))
        self.assertEqual({number: signal.getsignal(number) for number in helper.TERMINAL_SIGNALS},
                         before)
        self.assertEqual(set(helper.TERMINAL_SIGNALS),
                         {signal.SIGINT, signal.SIGTSTP, signal.SIGQUIT, signal.SIGHUP})

    def test_keyboard_interrupt_is_one_json_line_and_leaves_no_alarm(self):
        for count in (0, 1):
            with self.subTest(count=count):
                self.output.clear()
                self.rtc.writes.clear()
                self.interrupt_after(count, KeyboardInterrupt())
                self.boot += 100
                self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "interrupted"}])
                self.assertEqual(self.logged[-1], "set interrupted")
                self.assertEqual(self.alarm(), "")
                self.assertFalse((self.root.path/RECORD).exists())

    def test_any_exception_after_the_alarm_is_written_is_undone(self):
        self.record(NOW + 5000)
        self.set_alarm(NOW + 5000)
        self.rtc.hook = None
        calls = []
        real = helper.record_time

        def failing(context):
            calls.append(1)
            real(context)
            raise SystemExit(9)
        self.patch(helper, "record_time", failing)
        with self.assertRaises(SystemExit):
            self.main(["set", str(NOW + 600)])
        self.assertEqual(calls, [1])
        self.assertEqual(self.alarm(), "")
        self.assertFalse((self.root.path/RECORD).exists())

    def test_alarm_that_cannot_be_cancelled_keeps_a_matching_record(self):
        """取消也失敗時，紀錄改成 RTC 上實際的時間，之後的 clear 與移除套件仍能取消它。"""
        self.rtc.rounding = 30
        self.interrupt_after(2, OSError("refused"))
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
        self.assertEqual(self.alarm(), str(NOW + 630))
        self.assertEqual((self.root.path/RECORD).read_text(), f"{NOW + 630}\n")
        self.rtc.hook = None
        self.assertEqual(self.main(["clear"]), helper.EXIT_OK)
        self.assertEqual(self.alarm(), "")

    def test_interrupted_clear_is_one_json_line(self):
        self.main(["set", str(NOW + 600)])
        self.interrupt_after(len(self.rtc.writes), KeyboardInterrupt())
        self.assertEqual(self.main(["clear"]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "interrupted"})

    def test_main_ignores_terminal_signals_for_the_whole_run(self):
        text = (ROOT/"helper/sdm-wake").read_text(encoding="utf-8")
        entry = text[text.index('if __name__ == "__main__":'):]
        self.assertIn("ignore_terminal_signals()", entry)
        self.assertLess(entry.index("ignore_terminal_signals()"), entry.index("main("))

    def patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)


class Serialization(Base):
    def test_only_set_waits_ten_seconds_after_the_last_set(self):
        """取消隨時可以（關機或撤銷前不能被擋）；只有兩次 set 之間至少相隔 10 秒。"""
        self.boot_step = 0.0
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_OK)
        self.boot += 4
        self.assertEqual(self.main(["set", str(NOW + 900)]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "rate-limited",
                                           "retry_after": 6})
        self.assertEqual(self.main(["clear"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "clear", "cleared": True})
        self.assertEqual(self.alarm(), "")
        self.assertEqual(self.main(["clear"]), helper.EXIT_OK)
        self.assertEqual(self.main(["set", str(NOW + 900)]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["retry_after"], 6)
        self.boot += 6
        self.assertEqual(self.main(["set", str(NOW + 900)]), helper.EXIT_OK)

    def test_busy_when_another_run_holds_the_lock(self):
        self.main(["status"])
        fd = os.open(self.root.path/LOCK, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertEqual(self.main(["set", str(NOW + 600)]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "busy"})
        self.assertGreaterEqual(sum(self.sleeps), Wake.LOCK_WAIT)
        self.assertLess(sum(self.sleeps), Wake.LOCK_WAIT + 1)
        self.assertEqual(self.rtc.writes, [])
        self.assertEqual(self.logged[-1], "set busy")

    def test_lock_owned_by_someone_else_is_refused(self):
        self.root.file(LOCK, "", mode=0o666)
        self.assertEqual(self.main(["status"]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["error"], "untrusted-file")


class Consistency(unittest.TestCase):
    def test_constants_match_the_app(self):
        self.assertEqual(helper.PROTOCOL, Wake.PROTOCOL)
        self.assertEqual(tuple(layout.helper_dir + "/" + helper.HELPER_NAME
                               for layout in helper.LAYOUTS),
                         (Wake.DEB_HELPER, Wake.LOCAL_HELPER))
        self.assertEqual((helper.EARLIEST_S, helper.LATEST_S),
                         (SmartCharging.WAKE_EARLIEST, SmartCharging.WAKE_LATEST))
        self.assertEqual("/" + helper.RUN_DIR, ChargeLimit.RUN_DIR)
        self.assertEqual((helper.RECORD_NAME, helper.LOCK_NAME, helper.TIMES_NAME),
                         (Wake.RECORD_NAME, Wake.LOCK_NAME, Wake.TIMES_NAME))
        self.assertEqual((helper.MIN_WRITE_GAP_S, helper.LOCK_WAIT_S, helper.RTC_TOLERANCE_S),
                         (Wake.MIN_WRITE_GAP, Wake.LOCK_WAIT, Wake.RTC_TOLERANCE))
        self.assertEqual("/" + helper.RTC_DIR + "/" + helper.ALARM_FILE, Wake.RTC_ALARM)
        self.assertEqual((helper.EXIT_OK, helper.EXIT_USAGE, helper.EXIT_FAILED,
                          helper.EXIT_NOT_PERMITTED), CpuPower.EXIT_CODES)

    def test_helper_is_self_contained_and_isolated(self):
        text = (ROOT/"helper/sdm-wake").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/python3 -IB\n"))
        for forbidden in ("import sdm", "from sdm", "sys.path", "subprocess", "os.system",
                          "shell=True"):
            self.assertNotIn(forbidden, text)
        self.assertIn(git_mode(ROOT/"helper/sdm-wake"), (None, "100755"))


if __name__ == "__main__":
    unittest.main()
