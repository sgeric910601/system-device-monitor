"""充電上限的使用者端（sdm.services.charge_control）：狀態、拖動送出、讀回確認、失敗原因、
保留開關、自動寫入的排隊與示範模式。

假的 /sys（ThinkPad：上限 80、起點 75）放在暫存目錄；pkexec 以記錄呼叫的假執行器代替，
「小程式成功」時由測試把新值寫進假的 sysfs；計時器以假計時器代替。
"""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from sdm.constants import ChargeLimit
from sdm.core.battery import BatterySnapshot
from sdm.services.charge_control import (APPLIED, APPLYING, CHECKING, FAILED, IDLE,
                                         NOT_INSTALLED, UNSUPPORTED, ChargeControl,
                                         DemoChargeControl)
from tests.fixtures.fake_timers import FakeTimers
from tests.fixtures.sysfs import thinkpad
from tests.test_privileged import fake_tree

PKEXEC = "/usr/bin/pkexec"
SYSTEMCTL = "/usr/bin/systemctl"
HELPER = ChargeLimit.DEB_HELPER
PROTOCOL_OK = '{"protocol":1,"version":"1.0"}\n'
END = "sys/class/power_supply/BAT0/charge_control_end_threshold"


def ok_output(end, persist="off"):
    return json.dumps({"ok": True, "action": "set", "supply": "BAT0", "end": end, "start": 75,
                       "persist": persist}) + "\n"


class Runner:
    """假的執行器：記錄（參數、逾時、回呼），由測試決定何時完成。
    systemctl is-enabled 另外記在 unit_checks，依 enabled 立即回答（defer 時由測試回答）。"""

    def __init__(self):
        self.calls, self.unit_checks, self.enabled, self.defer = [], [], False, False

    def run(self, argv, timeout, done):
        if argv[0] == SYSTEMCTL:
            self.unit_checks.append((list(argv), timeout, done))
            if not self.defer:
                done(0 if self.enabled else 1, "")
            return
        self.calls.append((list(argv), timeout, done))

    def finish(self, status, output="", index=-1):
        self.calls[index][2](status, output)

    def writes(self):
        return [call[0] for call in self.calls if "set" in call[0]]


class Snapshot:
    def __init__(self, stamp, limit):
        self.stamp = stamp
        self.battery = BatterySnapshot(limit=limit, limit_supported=True)


class Base(unittest.TestCase):
    helpers = (HELPER,)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        thinkpad(self.root)
        self.timers, self.runner, self.changes, self.now = FakeTimers(), Runner(), [], 1000.0
        self.control = self.make()

    def make(self, start=True):
        control = ChargeControl(self.runner, lambda: self.changes.append(1), root=self.root,
                                lstat=fake_tree(*self.helpers), schedule=self.timers.schedule,
                                cancel=self.timers.cancel, wall=lambda: self.now)
        if start:
            control.refresh()
        return control

    def ready(self):
        self.runner.finish(0, PROTOCOL_OK)
        self.runner.calls.clear()

    def sysfs(self, end):
        (self.root/END).write_text(f"{end}\n")

    def save_state(self, supply="BAT0", end=80, enabled=True):
        """小程式 --persist on 的結果：狀態檔，以及（預設）已啟用的單元。"""
        path = self.root/ChargeLimit.DEB_STATE.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"format": 1, "supply": supply, "end": end, "start": None}))
        self.runner.enabled = enabled

    @property
    def state(self):
        return self.control.state


class Discovery(Base):
    def test_desktop_without_a_battery_is_unsupported(self):
        (self.root/"sys").rename(self.root/"gone")
        self.runner.calls.clear()
        self.control = self.make()
        self.assertEqual(self.state.status, UNSUPPORTED)

    def test_missing_helper_is_not_installed(self):
        self.helpers = ()
        self.runner.calls.clear()
        self.control = self.make()
        self.assertEqual(self.state.status, NOT_INSTALLED)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state.limit, 80)

    def test_protocol_is_checked_before_writes_are_allowed(self):
        self.assertEqual(self.state.status, CHECKING)
        argv, timeout, _ = self.runner.calls[0]
        self.assertEqual((argv, timeout), ([HELPER, "--protocol"], ChargeLimit.PROTOCOL_TIMEOUT))
        self.ready()
        self.assertEqual(self.state.status, IDLE)
        self.assertEqual((self.state.supply, self.state.limit, self.state.start,
                          self.state.has_start), ("BAT0", 80, 75, True))

    def test_protocol_mismatch_needs_a_reinstall(self):
        self.runner.finish(0, '{"protocol":2}\n')
        self.assertEqual(self.state.status, NOT_INSTALLED)
        self.assertIs(self.state.protocol, False)
        self.runner.finish(127, "")
        self.assertEqual(self.state.status, NOT_INSTALLED)

    def test_source_install_is_found(self):
        self.helpers = (ChargeLimit.LOCAL_HELPER,)
        self.runner.calls.clear()
        self.control = self.make()
        self.assertEqual(self.runner.calls[0][0], [ChargeLimit.LOCAL_HELPER, "--protocol"])

    def test_persisted_value_for_this_battery(self):
        self.save_state()
        self.control = self.make()
        self.assertTrue(self.state.persist)
        self.save_state(supply="BAT1")
        self.control.reload()
        self.assertFalse(self.state.persist)

    def test_persist_needs_the_state_file_and_the_enabled_unit(self):
        cases = ((True, True, True, False), (True, False, False, True),
                 (False, True, False, True), (False, False, False, False))
        for saved, enabled, persist, mismatch in cases:
            with self.subTest(saved=saved, enabled=enabled):
                path = self.root/ChargeLimit.DEB_STATE.lstrip("/")
                if saved:
                    self.save_state(enabled=enabled)
                else:
                    path.unlink(missing_ok=True)
                    self.runner.enabled = enabled
                self.control.reload()
                self.assertEqual((self.state.persist, self.state.persist_mismatch),
                                 (persist, mismatch))

    def test_unit_is_checked_without_privileges(self):
        self.runner.unit_checks.clear()
        self.control.reload()
        argv, timeout, _ = self.runner.unit_checks[0]
        self.assertEqual(argv, [SYSTEMCTL, "is-enabled", "--quiet", ChargeLimit.DEB_UNIT])
        self.assertEqual(timeout, ChargeLimit.UNIT_CHECK_TIMEOUT)

    def test_source_install_checks_its_own_unit(self):
        self.helpers = (ChargeLimit.LOCAL_HELPER,)
        self.control = self.make()
        self.assertEqual(self.runner.unit_checks[-1][0][-1], ChargeLimit.LOCAL_UNIT)

    def test_unknown_unit_state_is_neither_on_nor_a_mismatch(self):
        self.save_state()
        self.runner.defer = True
        self.control.reload()
        self.runner.unit_checks[-1][2](None, "")
        self.assertEqual((self.state.persist, self.state.persist_mismatch), (False, False))

    def test_late_unit_answers_are_ignored(self):
        self.save_state()
        self.runner.defer = True
        self.control.reload()
        first = self.runner.unit_checks[-1][2]
        self.control.reload()
        self.runner.unit_checks[-1][2](0, "")
        first(1, "")
        self.assertEqual((self.state.persist, self.state.persist_mismatch), (True, False))

    def test_no_unit_check_without_helper_or_battery(self):
        self.helpers = ()
        self.runner.unit_checks.clear()
        self.control = self.make()
        self.assertEqual(self.runner.unit_checks, [])

    def test_unlimited_reads_as_one_hundred(self):
        self.sysfs(100)
        self.control.reload()
        self.assertEqual(self.state.limit, 100)
        self.sysfs(0)
        self.control.reload()
        self.assertEqual(self.state.limit, 100)


class UserWrites(Base):
    def setUp(self):
        super().setUp()
        self.ready()

    def test_drag_commits_after_the_pause_and_confirms(self):
        self.control.drag(85)
        self.assertEqual((self.state.draft, self.state.shown), (85, 85))
        self.timers.advance(0.59)
        self.assertEqual(self.runner.calls, [])
        self.timers.advance(0.02)
        argv, timeout, _ = self.runner.calls[0]
        self.assertEqual(argv, [PKEXEC, HELPER, "set", "BAT0", "85", "--persist", "off"])
        self.assertEqual(timeout, ChargeLimit.PKEXEC_TIMEOUT)
        self.assertEqual(self.state.status, APPLYING)
        self.sysfs(85)
        self.runner.finish(0, ok_output(85))
        self.assertEqual((self.state.status, self.state.limit, self.state.draft),
                         (APPLIED, 85, None))

    def test_quick_changes_write_only_the_last_value(self):
        self.control.drag(85)
        self.timers.advance(0.3)
        self.control.drag(90)
        self.timers.advance(1)
        self.assertEqual([argv[4] for argv in self.runner.writes()], ["90"])

    def test_release_writes_immediately(self):
        self.control.set_editing(True)
        self.control.drag(90)
        self.timers.advance(3)
        self.assertEqual(self.runner.calls, [])
        self.control.set_editing(False)
        self.assertEqual(len(self.runner.writes()), 1)

    def test_back_to_the_current_value_writes_nothing(self):
        self.control.drag(85)
        self.control.drag(80)
        self.timers.advance(1)
        self.assertEqual(self.runner.calls, [])
        self.assertIsNone(self.state.draft)

    def test_moving_during_a_write_writes_again_afterwards(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.control.drag(95)
        self.timers.advance(1)
        self.assertEqual(len(self.runner.writes()), 1)
        self.sysfs(85)
        self.runner.finish(0, ok_output(85))
        self.assertEqual([argv[4] for argv in self.runner.writes()], ["85", "95"])

    def test_read_back_is_polled_three_times(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(0, ok_output(85))
        self.assertEqual(self.state.status, APPLYING)
        self.timers.advance(ChargeLimit.READBACK_INTERVAL)
        self.sysfs(85)
        self.timers.advance(ChargeLimit.READBACK_INTERVAL)
        self.assertEqual((self.state.status, self.state.limit), (APPLIED, 85))

    def test_read_back_mismatch_fails(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(0, ok_output(85))
        self.timers.advance(2 * ChargeLimit.READBACK_INTERVAL)
        self.assertEqual((self.state.status, self.state.limit), (FAILED, 80))
        self.assertIn("80%", self.state.message)

    def test_driver_that_refuses_a_value_offers_what_it_accepts(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(3, json.dumps({"ok": False, "error": "not-applied", "applied": 80,
                                          "applied_start": 75}))
        self.assertEqual((self.state.status, self.state.limit, self.state.draft),
                         (FAILED, 80, None))
        self.assertEqual(self.state.options, (80, 90, 95, 100))
        self.assertIn("85%", self.state.message)
        self.assertIn("80%", self.state.message)

    def test_unauthorized(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(126, "")
        self.assertEqual((self.state.status, self.state.limit, self.state.draft),
                         (FAILED, 80, None))
        self.assertIn("未授權", self.state.message)

    def test_timeout(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(None, "")
        self.assertEqual(self.state.status, FAILED)
        self.assertIn("逾時", self.state.message)

    def test_persist_failure_keeps_the_applied_limit(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(3, json.dumps({"ok": False, "error": "persist-failed", "applied": 85,
                                          "applied_start": 75}))
        self.assertEqual((self.state.status, self.state.limit), (FAILED, 85))
        self.assertIn("保留", self.state.message)

    def test_another_run_in_progress(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(3, '{"ok":false,"error":"busy"}\n')
        self.assertEqual(self.state.status, FAILED)
        self.assertIn("另一個", self.state.message)

    def test_other_helper_failures(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(3, '{"ok":false,"error":"not-a-battery"}\n')
        self.assertEqual(self.state.status, FAILED)
        self.assertTrue(self.state.message)

    def test_dragging_clears_a_previous_failure(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.runner.finish(126, "")
        self.control.drag(90)
        self.assertEqual(self.state.status, IDLE)

    def test_persist_on_is_passed_along(self):
        self.save_state()
        self.control.reload()
        self.control.drag(90)
        self.timers.advance(1)
        self.assertEqual(self.runner.writes()[0][-2:], ["--persist", "on"])


class NotReady(Base):
    def test_drags_never_run_the_helper_until_ready(self):
        self.control.drag(90)
        self.timers.advance(1)
        self.assertEqual(self.runner.writes(), [])
        self.assertIsNone(self.state.draft)


class Persist(Base):
    def setUp(self):
        super().setUp()
        self.ready()

    def test_switching_on_rewrites_the_current_limit_with_persistence(self):
        self.control.set_persist(True)
        self.assertEqual(self.runner.writes(), [[PKEXEC, HELPER, "set", "BAT0", "80",
                                                 "--persist", "on"]])
        self.save_state()
        self.runner.finish(0, ok_output(80, "on"))
        self.assertTrue(self.state.persist)
        self.assertEqual(self.state.status, APPLIED)

    def test_switching_off(self):
        self.save_state()
        self.control.reload()
        self.control.set_persist(False)
        self.assertEqual(self.runner.writes()[0][-2:], ["--persist", "off"])

    def test_no_change_needs_no_authorization(self):
        self.control.set_persist(False)
        self.assertEqual(self.runner.calls, [])

    def test_switch_on_repairs_a_mismatch(self):
        self.save_state(enabled=False)
        self.control.reload()
        self.assertTrue(self.state.persist_mismatch)
        self.control.set_persist(True)
        self.assertEqual(self.runner.writes()[0][-2:], ["--persist", "on"])
        self.runner.enabled = True
        self.runner.finish(0, ok_output(80, "on"))
        self.assertEqual((self.state.persist, self.state.persist_mismatch), (True, False))

    def test_failed_switch_keeps_the_real_state(self):
        self.control.set_persist(True)
        self.runner.finish(126, "")
        self.assertFalse(self.state.persist)


class Automatic(Base):
    def setUp(self):
        super().setUp()
        self.ready()
        self.results = []

    def done(self, ok, value):
        self.results.append((ok, value))

    def test_applies_without_touching_persistence(self):
        self.control.apply_automatically(90, self.done)
        self.assertEqual(self.runner.writes(), [[PKEXEC, HELPER, "set", "BAT0", "90"]])
        self.sysfs(90)
        self.runner.finish(0, ok_output(90))
        self.assertEqual(self.results, [(True, 90)])

    def test_waits_for_the_user_edit_first(self):
        self.control.drag(85)
        self.control.apply_automatically(95, self.done)
        self.assertEqual(self.runner.calls, [])
        self.timers.advance(0.6)
        self.assertEqual([argv[4] for argv in self.runner.writes()], ["85"])
        self.sysfs(85)
        self.runner.finish(0, ok_output(85))
        self.timers.advance(0.2)
        self.assertEqual([argv[4] for argv in self.runner.writes()], ["85", "95"])

    def test_gives_up_after_ten_seconds(self):
        self.control.set_editing(True)
        self.control.drag(85)
        self.control.apply_automatically(95, self.done)
        self.timers.advance(ChargeLimit.AUTOMATIC_WAIT - 0.5)
        self.assertEqual(self.results, [])
        self.timers.advance(1)
        self.assertEqual(self.results, [(False, 80)])
        self.assertEqual(self.runner.calls, [])

    def test_value_already_in_place(self):
        self.control.apply_automatically(80, self.done)
        self.assertEqual((self.results, self.runner.calls), ([(True, 80)], []))

    def test_value_that_is_not_an_option(self):
        self.control.apply_automatically(87, self.done)
        self.assertEqual(self.results, [(False, None)])

    def test_persisted_user_value_is_left_alone(self):
        self.save_state()
        self.control.reload()
        self.control.apply_automatically(90, self.done)
        self.assertNotIn("--persist", self.runner.writes()[0])

    def test_queued_requests_run_in_order(self):
        self.control.apply_automatically(90, self.done)
        self.control.apply_automatically(95, self.done)
        self.assertEqual(len(self.runner.writes()), 1)
        self.sysfs(90)
        self.runner.finish(0, ok_output(90))
        self.assertEqual([argv[4] for argv in self.runner.writes()], ["90", "95"])

    def test_user_drag_during_an_automatic_write_is_written_afterwards(self):
        self.control.apply_automatically(90, self.done)
        self.control.drag(85)
        self.timers.advance(1)
        self.assertEqual(len(self.runner.writes()), 1)
        self.sysfs(90)
        self.runner.finish(0, ok_output(90))
        self.assertEqual([argv[4] for argv in self.runner.writes()], ["90", "85"])

    def test_not_ready(self):
        self.control.state = replace(self.control.state, protocol=False)
        self.control.apply_automatically(90, self.done)
        self.assertEqual(self.results, [(False, None)])


class Observe(Base):
    def setUp(self):
        super().setUp()
        self.ready()

    def test_snapshots_update_the_limit(self):
        self.control.observe(Snapshot(self.now, 90))
        self.assertEqual(self.state.limit, 90)
        self.control.observe(Snapshot(self.now, None))
        self.assertEqual(self.state.limit, 100)

    def test_snapshots_do_not_move_the_slider_while_editing(self):
        self.control.drag(85)
        self.control.observe(Snapshot(self.now, 90))
        self.assertEqual(self.state.shown, 85)

    def test_older_snapshots_after_a_write_are_ignored(self):
        self.control.drag(85)
        self.timers.advance(1)
        self.sysfs(85)
        self.runner.finish(0, ok_output(85))
        self.control.observe(Snapshot(self.now - 1, None))
        self.assertEqual(self.state.limit, 85)

    def test_other_snapshots_are_ignored(self):
        self.control.observe(None)
        self.control.observe(object())
        self.assertEqual(self.state.limit, 80)


class Stop(Base):
    def test_late_results_are_ignored(self):
        self.ready()
        self.control.drag(85)
        self.timers.advance(1)
        self.control.stop()
        count = len(self.changes)
        self.runner.finish(0, ok_output(85))
        self.timers.advance(5)
        self.assertEqual(len(self.changes), count)

    def test_pending_drag_is_dropped(self):
        self.ready()
        self.control.drag(85)
        self.control.stop()
        self.timers.advance(1)
        self.assertEqual(self.runner.calls, [])


class Demo(unittest.TestCase):
    def setUp(self):
        self.timers, self.changes = FakeTimers(), []
        self.control = DemoChargeControl(lambda: self.changes.append(1), self.timers.schedule,
                                         self.timers.cancel, limit=80)

    def test_shows_a_ready_slider_without_any_helper(self):
        state = self.control.state
        self.assertEqual((state.status, state.limit, state.demo), (IDLE, 80, True))
        self.assertFalse(hasattr(self.control, "runner"))

    def test_drag_only_changes_memory(self):
        self.control.drag(90)
        self.timers.advance(1)
        self.assertEqual((self.control.state.limit, self.control.state.status), (90, APPLIED))

    def test_persist_and_automatic_writes_stay_in_memory(self):
        self.control.set_persist(True)
        self.assertTrue(self.control.state.persist)
        results = []
        self.control.apply_automatically(95, lambda ok, value: results.append((ok, value)))
        self.assertEqual(results, [(True, 95)])
        for method in (self.control.refresh, self.control.reload, self.control.stop):
            method()
        self.control.observe(None)


if __name__ == "__main__":
    unittest.main()
