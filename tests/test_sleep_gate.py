"""logind 的睡眠延遲（sdm.services.sleep_gate）：只在有排程時拿著延遲鎖、即將睡眠時做完
（或最多 4 秒）就放開、喚醒後重新拿、關機前也有機會取消喚醒鬧鐘。

以假的匯流排連線代替 logind（不連任何匯流排）；延遲鎖是真的管道描述子，
放開時讀取端會讀到結尾。實際經由 D-Bus 傳遞描述子的測試在 test_sleep_gate_bus。
"""
import os
import unittest

from gi.repository import GLib

from sdm.constants import SmartCharging
from sdm.services.sleep_gate import (MANAGER, MODE, PATH, SERVICE, WHAT, WHO, WHY, SleepGate)
from tests.fixtures.fake_timers import FakeTimers


class FdList:
    def __init__(self, fds):
        self.fds = list(fds)

    def steal_fds(self):
        fds, self.fds = self.fds, []
        return fds


class Variant:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class Connection:
    """假的系統匯流排：記錄 Inhibit 呼叫與訂閱的訊號，由測試決定何時回答。"""

    def __init__(self):
        self.calls, self.handlers, self.next_id = [], {}, 1

    def signal_subscribe(self, sender, interface, member, path, arg0, flags, callback):
        self.handlers[self.next_id] = (sender, interface, member, path, callback)
        self.next_id += 1
        return self.next_id - 1

    def signal_unsubscribe(self, subscription):
        self.handlers.pop(subscription)

    def call_with_unix_fd_list(self, name, path, interface, method, parameters, reply_type,
                               flags, timeout, fd_list, cancellable, callback, *data):
        self.calls.append(dict(name=name, path=path, interface=interface, method=method,
                               args=parameters.unpack(), reply=reply_type.dup_string(),
                               callback=callback, data=data, timeout=timeout))

    def call_with_unix_fd_list_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result

    def answer(self, index=-1, error=None):
        """回答一個 Inhibit：回傳（讀取端, 寫入端）；寫入端交給被測的程式。"""
        read, write = os.pipe()
        os.set_blocking(read, False)
        call = self.calls[index]
        result = error or (Variant((1,)), FdList([os.open(os.devnull, os.O_RDONLY), write]))
        call["callback"](self, result, *call["data"])
        return read

    def emit(self, member, value):
        for sender, interface, name, path, callback in list(self.handlers.values()):
            if name == member and (sender, interface, path) == (SERVICE, MANAGER, PATH):
                callback(self, sender, path, interface, member, GLib.Variant("(b)", (value,)))


def released(read):
    """延遲鎖的寫入端都關了（讀到結尾）。"""
    try:
        return os.read(read, 1) == b""
    except BlockingIOError:
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.connection, self.timers = Connection(), FakeTimers()
        self.events, self.pending = [], []
        self.gate = SleepGate(lambda: self.connection, self.will_sleep, self.did_wake,
                              self.shutdown, schedule=self.timers.schedule,
                              cancel=self.timers.cancel, clock=lambda: self.timers.now)
        self.gate.start()
        self.reads = []

    def tearDown(self):
        self.gate.stop()
        for read in self.reads:
            os.close(read)

    def will_sleep(self, done):
        self.events.append("will_sleep")
        self.pending.append(done)

    def did_wake(self):
        self.events.append("did_wake")

    def shutdown(self, done):
        self.events.append("shutdown")
        self.pending.append(done)

    def take(self):
        self.gate.set_active(True)
        read = self.connection.answer()
        self.reads.append(read)
        return read


class Lifecycle(Base):
    def test_nothing_is_taken_without_a_plan(self):
        self.assertEqual(self.connection.calls, [])
        self.gate.set_active(False)
        self.assertEqual(self.connection.calls, [])
        self.assertFalse(self.gate.holding)

    def test_takes_one_delay_lock_while_active(self):
        read = self.take()
        call = self.connection.calls[0]
        self.assertEqual((call["name"], call["path"], call["interface"], call["method"]),
                         (SERVICE, PATH, MANAGER, "Inhibit"))
        self.assertEqual(call["args"], (WHAT, WHO, WHY, MODE))
        self.assertEqual((WHAT, MODE), ("sleep:shutdown", "delay"))
        self.assertEqual(call["reply"], "(h)")
        self.assertTrue(self.gate.holding)
        self.assertFalse(released(read))
        self.gate.set_active(True)
        self.assertEqual(len(self.connection.calls), 1)

    def test_releases_when_the_plan_ends(self):
        read = self.take()
        self.gate.set_active(False)
        self.assertTrue(released(read))
        self.assertFalse(self.gate.holding)

    def test_answer_after_the_plan_ended_is_closed_at_once(self):
        self.gate.set_active(True)
        self.gate.set_active(False)
        read = self.connection.answer()
        self.reads.append(read)
        self.assertTrue(released(read))
        self.assertFalse(self.gate.holding)

    def test_stop_unsubscribes_and_releases(self):
        read = self.take()
        self.gate.stop()
        self.assertTrue(released(read))
        self.assertEqual(self.connection.handlers, {})

    def test_no_bus_does_nothing(self):
        gate = SleepGate(lambda: None, self.will_sleep, self.did_wake)
        gate.start()
        gate.set_active(True)
        self.assertFalse(gate.holding)
        gate.stop()


class Sleep(Base):
    def test_released_as_soon_as_the_plan_is_ready_for_sleep(self):
        read = self.take()
        self.connection.emit("PrepareForSleep", True)
        self.assertEqual(self.events, ["will_sleep"])
        self.assertFalse(released(read))
        self.pending.pop()()
        self.assertTrue(released(read))

    def test_released_within_five_seconds_even_if_the_write_hangs(self):
        read = self.take()
        self.connection.emit("PrepareForSleep", True)
        self.timers.advance(SmartCharging.SLEEP_RELEASE_SECONDS - 0.1)
        self.assertFalse(released(read))
        self.timers.advance(0.1)
        self.assertTrue(released(read))
        self.assertLess(SmartCharging.SLEEP_RELEASE_SECONDS, 5)
        self.pending.pop()()                    # 之後才完成也沒有影響
        self.assertEqual(self.timers.pending, {})

    def test_release_by_the_timer_is_a_warning(self):
        """時間到才放開（不是寫完）時記警告，並告訴排程是睡眠還是關機。"""
        timed_out = []
        self.gate.timed_out = timed_out.append
        read = self.take()
        self.connection.emit("PrepareForSleep", True)
        with self.assertLogs("sdm.services.sleep_gate", "WARNING") as logs:
            self.timers.advance(SmartCharging.SLEEP_RELEASE_SECONDS)
        self.assertTrue(released(read))
        self.assertEqual(timed_out, ["sleep"])
        self.assertIn("before smart charging finished", logs.output[0])
        self.pending.pop()()
        self.assertEqual(timed_out, ["sleep"])

    def test_release_after_finishing_is_not_a_warning(self):
        timed_out = []
        self.gate.timed_out = timed_out.append
        self.take()
        self.connection.emit("PrepareForShutdown", True)
        with self.assertNoLogs("sdm.services.sleep_gate", "WARNING"):
            self.pending.pop()()
            self.timers.advance(SmartCharging.SLEEP_RELEASE_SECONDS)
        self.assertEqual(timed_out, [])

    def test_taken_again_after_waking_up(self):
        self.take()
        self.connection.emit("PrepareForSleep", True)
        self.pending.pop()()
        self.connection.emit("PrepareForSleep", False)
        self.assertEqual(self.events, ["will_sleep", "did_wake"])
        self.assertEqual(len(self.connection.calls), 2)
        self.reads.append(self.connection.answer())
        self.assertTrue(self.gate.holding)

    def test_not_taken_again_between_sleep_and_wake(self):
        """放開後到真正睡著前若又判斷一次，不能再拿新的鎖（logind 會等它到逾時）。"""
        self.take()
        self.connection.emit("PrepareForSleep", True)
        self.pending.pop()()
        self.gate.set_active(True)
        self.assertEqual(len(self.connection.calls), 1)
        self.connection.emit("PrepareForSleep", False)
        self.assertEqual(len(self.connection.calls), 2)

    def test_missing_wake_signal_is_given_up_after_two_awake_minutes(self):
        self.take()
        self.connection.emit("PrepareForSleep", True)
        self.pending.pop()()
        self.timers.advance(SmartCharging.SLEEP_GUARD_SECONDS)
        self.gate.set_active(True)
        self.assertEqual(len(self.connection.calls), 2)

    def test_not_taken_again_when_the_plan_ended_before_waking(self):
        self.take()
        self.connection.emit("PrepareForSleep", True)
        self.gate.set_active(False)
        self.connection.emit("PrepareForSleep", False)
        self.assertEqual(len(self.connection.calls), 1)

    def test_sleep_without_a_lock_still_tells_the_plan(self):
        self.connection.emit("PrepareForSleep", True)
        self.assertEqual(self.events, ["will_sleep"])
        self.pending.pop()()

    def test_shutdown_lets_the_plan_cancel_the_wake_alarm(self):
        read = self.take()
        self.connection.emit("PrepareForShutdown", True)
        self.assertEqual(self.events, ["shutdown"])
        self.pending.pop()()
        self.assertTrue(released(read))
        self.connection.emit("PrepareForShutdown", False)      # 取消關機
        self.assertEqual(len(self.connection.calls), 2)

    def test_signals_from_other_senders_are_not_subscribed(self):
        senders = {handler[0] for handler in self.connection.handlers.values()}
        members = sorted(handler[2] for handler in self.connection.handlers.values())
        self.assertEqual((senders, members), ({SERVICE}, ["PrepareForShutdown",
                                                           "PrepareForSleep"]))


class Failures(Base):
    def test_refused_lock_is_retried_after_a_minute(self):
        self.gate.set_active(True)
        with self.assertLogs("sdm.services.sleep_gate", "WARNING"):
            self.connection.answer(error=GLib.Error("denied"))
        self.assertFalse(self.gate.holding)
        self.gate.set_active(True)
        self.assertEqual(len(self.connection.calls), 1)
        self.timers.advance(60)
        self.gate.set_active(True)
        self.assertEqual(len(self.connection.calls), 2)

    def test_reply_without_a_descriptor(self):
        self.gate.set_active(True)
        call = self.connection.calls[0]
        with self.assertLogs("sdm.services.sleep_gate", "WARNING"):
            call["callback"](self.connection, (Variant((3,)), FdList([])), *call["data"])
        self.assertFalse(self.gate.holding)


if __name__ == "__main__":
    unittest.main()
