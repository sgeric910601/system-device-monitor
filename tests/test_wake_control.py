"""精準喚醒的看板端（sdm.services.wake_control）：移植參考專案 ChargingWakeScheduler 的規則。

- 只在選了精準喚醒、元件可用而且已允許無人時自動調整時才排定；
- 喚醒時間只能在 60 秒後到 7 天內；與已排定的時間相差 5 分鐘以上才重排，1 秒內視為同一個；
- 不需要時取消；失敗後 1 分鐘內不再試；兩次 set 至少相隔 10 秒，取消不必等；
- 已排定的鬧鐘不見了（已經響過或被別人換掉）就不算排好。
pkexec 以假的執行器代替，RTC 讀值以假函式代替，時間以假時鐘代替。
"""
import json
import unittest

from sdm.constants import CpuPower, Wake
from sdm.services.wake_control import (FAILED, IDLE, MISSING, NOT_GRANTED, OFF, SCHEDULED,
                                       DemoWake, WakeControl, read_rtc_alarm)
from tests.fixtures.fake_timers import FakeTimers
from tests.test_privileged import fake_tree

PKEXEC = CpuPower.PKEXEC
NOW = 1_800_000_000.0
PROTOCOL_OK = '{"protocol":1,"version":"1.0"}\n'


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout, done):
        self.calls.append((list(argv), timeout, done))

    def finish(self, status, output="", index=-1):
        self.calls[index][2](status, output)

    def helper_calls(self):
        return [call[0][2:] for call in self.calls if call[0][0] == PKEXEC]


def set_output(epoch):
    return json.dumps({"ok": True, "action": "set", "alarm": epoch})


class Base(unittest.TestCase):
    helpers = (Wake.DEB_HELPER,)

    def setUp(self):
        self.runner, self.timers, self.changes = Runner(), FakeTimers(), []
        self.now, self.alarm = NOW, None
        self.control = WakeControl(self.runner, lambda: self.changes.append(1),
                                   lstat=fake_tree(*self.helpers), schedule=self.timers.schedule,
                                   cancel=self.timers.cancel, clock=lambda: self.timers.now,
                                   wall=lambda: self.now, read_alarm=lambda: self.alarm)
        self.control.refresh()
        if self.runner.calls:
            self.runner.finish(0, PROTOCOL_OK)

    @property
    def state(self):
        return self.control.state

    def sync(self, wake_at, enabled=True, granted=True):
        self.control.sync(wake_at, enabled, granted)

    def scheduled(self, epoch):
        """假的小程式成功排定：RTC 讀到的值跟著改。"""
        self.alarm = epoch
        self.runner.finish(0, set_output(epoch))

    def advance(self, seconds):
        self.now += seconds
        self.timers.advance(seconds)


class Availability(Base):
    def test_nothing_runs_without_precise_wake_or_the_grant(self):
        self.sync(NOW + 3600, enabled=False)
        self.assertEqual(self.state.note, OFF)
        self.sync(NOW + 3600, granted=False)
        self.assertEqual(self.state.note, NOT_GRANTED)
        self.assertEqual(self.runner.helper_calls(), [])
        self.assertFalse(self.control.ready())

    def test_missing_component(self):
        self.helpers = ()
        self.setUp()
        self.sync(NOW + 3600)
        self.assertEqual((self.state.note, self.runner.calls), (MISSING, []))

    def test_wrong_protocol_counts_as_missing(self):
        self.runner.calls.clear()
        self.control.refresh()
        self.runner.finish(0, '{"protocol":7}\n')
        self.sync(NOW + 3600)
        self.assertEqual(self.state.note, MISSING)

    def test_reading_the_rtc_alarm(self):
        self.assertIsNone(read_rtc_alarm("/nonexistent/wakealarm"))


class Scheduling(Base):
    def test_sets_the_alarm_through_pkexec(self):
        self.sync(NOW + 3600.7)
        argv, timeout, _ = self.runner.calls[-1]
        self.assertEqual(argv, [PKEXEC, Wake.DEB_HELPER, "set", str(int(NOW) + 3600)])
        self.assertEqual(timeout, Wake.PKEXEC_TIMEOUT)
        self.scheduled(int(NOW) + 3600)
        self.assertEqual((self.state.note, self.state.scheduled), (SCHEDULED, int(NOW) + 3600))
        self.assertTrue(self.control.ready())

    def test_keeps_an_alarm_within_one_second_and_moves_only_five_minutes_or_more(self):
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.advance(20)
        for wake in (NOW + 3600.5, NOW + 3600 + 120, NOW + 3600 - 299):
            with self.subTest(wake=wake):
                self.sync(wake)
                self.assertEqual(len(self.runner.helper_calls()), 1)
        self.sync(NOW + 3600 + 600)
        self.assertEqual(self.runner.helper_calls()[-1], ["set", str(int(NOW) + 4200)])

    def test_too_soon_or_no_wake_clears_the_alarm(self):
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.advance(20)
        self.sync(None)
        self.assertEqual(self.runner.helper_calls()[-1], ["clear"])
        self.alarm = None
        self.runner.finish(0, json.dumps({"ok": True, "action": "clear", "cleared": True}))
        self.assertEqual((self.state.scheduled, self.state.note), (None, IDLE))
        self.advance(20)
        self.sync(NOW + 30)
        self.assertEqual(len(self.runner.helper_calls()), 2)
        self.assertEqual(self.state.note, IDLE)

    def test_turning_precise_wake_off_clears_the_alarm(self):
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.advance(20)
        self.sync(NOW + 3600, enabled=False)
        self.assertEqual(self.runner.helper_calls()[-1], ["clear"])

    def test_more_than_seven_days_ahead_is_not_requested(self):
        self.sync(NOW + 8 * 24 * 3600)
        self.assertEqual(self.runner.helper_calls(), [])

    def test_ten_seconds_between_helper_runs(self):
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.sync(NOW + 7200)
        self.assertEqual(len(self.runner.helper_calls()), 1)
        self.advance(Wake.MIN_WRITE_GAP)
        self.assertEqual(self.runner.helper_calls()[-1], ["set", str(int(NOW) + 7200)])

    def test_clear_runs_at_once_but_set_waits_ten_seconds_after_the_last_set(self):
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.sync(None)
        self.assertEqual(self.runner.helper_calls()[-1], ["clear"])
        self.alarm = None
        self.runner.finish(0, json.dumps({"ok": True, "action": "clear", "cleared": True}))
        self.advance(4)
        self.sync(NOW + 7200)
        self.assertEqual(len(self.runner.helper_calls()), 2)
        self.advance(Wake.MIN_WRITE_GAP - 4)
        self.assertEqual(self.runner.helper_calls()[-1], ["set", str(int(NOW) + 7200)])

    def test_one_run_at_a_time_then_the_latest_request(self):
        self.sync(NOW + 3600)
        self.sync(NOW + 9000)
        self.assertEqual(len(self.runner.helper_calls()), 1)
        self.scheduled(int(NOW) + 3600)
        self.advance(Wake.MIN_WRITE_GAP)
        self.assertEqual(self.runner.helper_calls()[-1], ["set", str(int(NOW) + 9000)])


class Failures(Base):
    def test_failure_is_explained_and_retried_after_a_minute(self):
        self.sync(NOW + 3600)
        self.runner.finish(3, '{"ok":false,"error":"alarm-in-use"}')
        self.assertEqual((self.state.note, self.state.error), (FAILED, "alarm-in-use"))
        self.assertIsNone(self.state.scheduled)
        self.assertFalse(self.control.ready())
        self.advance(30)
        self.sync(NOW + 3600)
        self.assertEqual(len(self.runner.helper_calls()), 1)
        self.advance(31)
        self.sync(NOW + 3600)
        self.assertEqual(len(self.runner.helper_calls()), 2)

    def test_cancelled_authorization(self):
        self.sync(NOW + 3600)
        self.runner.finish(126, "")
        self.assertEqual((self.state.note, self.state.error), (FAILED, "unauthorized"))

    def test_alarm_that_went_off_or_was_replaced_is_not_ready(self):
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.alarm = None
        self.assertFalse(self.control.ready())
        self.advance(20)
        self.sync(NOW - 10)
        self.assertIsNone(self.state.scheduled)
        self.assertEqual(self.runner.helper_calls(), [["set", str(int(NOW) + 3600)]])


class Shutdown(Base):
    def test_clear_before_shutdown_only_when_an_alarm_is_set(self):
        results = []
        self.control.clear_now(lambda: results.append("done"))
        self.assertEqual((results, self.runner.helper_calls()), (["done"], []))
        self.sync(NOW + 3600)
        self.scheduled(int(NOW) + 3600)
        self.control.clear_now(lambda: results.append("cleared"))
        self.assertEqual(self.runner.helper_calls()[-1], ["clear"])
        self.runner.finish(0, json.dumps({"ok": True, "action": "clear", "cleared": True}))
        self.assertEqual(results, ["done", "cleared"])


class Demo(unittest.TestCase):
    def test_demo_only_remembers_the_time(self):
        demo = DemoWake(lambda: None, clock=lambda: NOW)
        demo.sync(NOW + 3600, True, True)
        self.assertEqual((demo.state.scheduled, demo.state.note, demo.state.demo),
                         (int(NOW) + 3600, SCHEDULED, True))
        self.assertTrue(demo.ready())
        demo.sync(None, True, True)
        self.assertIsNone(demo.state.scheduled)


if __name__ == "__main__":
    unittest.main()
