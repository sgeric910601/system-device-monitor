"""看板的充電上限接線（sdm.ui.charge_limit_wiring）：示範模式不執行任何程式、滑桿放進電源頁、
開啟程式時只讀取不授權、衝突偵測在背景執行，以及顯示與否改變時重畫詳細資訊。

不建立 GTK 元件：滑桿以假的元件代替；執行緒池與主迴圈以立即執行的假物件代替。
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdm.data.charge_conflicts import Conflict
from sdm.services.charge_control import ChargeControl, ChargeState, DemoChargeControl, LAYOUTS
from sdm.ui import charge_limit_wiring as wiring

READY = ChargeState(supply="BAT0", limit=80, layout=LAYOUTS[0], protocol=True)


class FakeView:
    def __init__(self, control):
        self.control, self.synced = control, 0

    def sync(self):
        self.synced += 1


class FakePage:
    def __init__(self):
        self.limit, self.redraws = None, 0

    def set_charge_limit(self, widget):
        self.limit = widget

    def settings_changed(self):
        self.redraws += 1


class FakeControl:
    def __init__(self, state=READY):
        self.state, self.calls, self.conflicts = state, [], None

    def refresh(self):
        self.calls.append("refresh")

    def observe(self, power):
        self.calls.append(("observe", power))

    def set_conflicts(self, conflicts):
        self.conflicts = conflicts

    def stop(self):
        self.calls.append("stop")


class Future:
    def __init__(self, function, *args, **kwargs):
        self.function, self.args, self.kwargs = function, args, kwargs

    def add_done_callback(self, callback):
        callback(self)

    def result(self):
        return self.function(*self.args, **self.kwargs)


class Pool:
    def __init__(self):
        self.submitted = []

    def submit(self, function, *args, **kwargs):
        self.submitted.append((function, args, kwargs))
        return Future(function, *args, **kwargs)


class Host(wiring.ChargeLimitMixin):
    def __init__(self, preview=None):
        self.preview, self.closing, self.settings_window = preview, False, None
        self.power_page, self.pool, self.power = FakePage(), Pool(), object()
        self.collector = SimpleNamespace(bus=None)
        self.opened = []

    def make_charge_view(self, control):
        return FakeView(control)

    def current_power(self):
        return self.power

    def open_settings(self, section=None):
        self.opened.append(section)

    @staticmethod
    def charge_on_main(function, *args):
        function(*args)


class Build(unittest.TestCase):
    def test_preview_uses_the_demo_and_never_creates_a_runner(self):
        with patch.object(wiring, "HelperRunner", side_effect=AssertionError("runner")):
            host = Host("laptop-limit")
            host.build_charge_limit()
            host.start_charge_limit()
        self.assertIsInstance(host.charge_control, DemoChargeControl)
        self.assertEqual(host.charge_control.state.limit, 80)
        self.assertIs(host.power_page.limit, host.charge_view)
        self.assertEqual(host.pool.submitted, [])

    def test_desktop_preview_has_an_unused_demo(self):
        host = Host("desktop")
        host.build_charge_limit()
        self.assertIsInstance(host.charge_control, DemoChargeControl)

    def test_normal_run_uses_the_admin_helper(self):
        runners = []
        with patch.object(wiring, "HelperRunner", side_effect=lambda: runners.append(1) or "r"):
            host = Host()
            host.build_charge_limit()
        self.assertIsInstance(host.charge_control, ChargeControl)
        self.assertEqual((runners, host.charge_control.runner), ([1], "r"))


class Running(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.control = FakeControl()
        self.host.charge_control = self.control
        self.host.charge_view = FakeView(self.control)

    def test_start_reads_and_detects_conflicts_in_the_background(self):
        found = (Conflict("tlp", "甲。"),)
        with patch.object(wiring, "detect_conflicts", return_value=found) as detect:
            self.host.start_charge_limit()
        self.assertEqual(self.control.calls, ["refresh"])
        self.assertEqual(detect.call_args.args, ("BAT0",))
        self.assertIsNone(detect.call_args.kwargs["upower"])
        self.assertEqual(self.control.conflicts, found)

    def test_no_detection_without_an_adjustable_battery(self):
        self.control.state = ChargeState()
        with patch.object(wiring, "detect_conflicts") as detect:
            self.host.start_charge_limit()
        detect.assert_not_called()

    def test_failed_detection_is_only_logged(self):
        with patch.object(wiring, "detect_conflicts", side_effect=OSError("x")):
            with self.assertLogs(wiring.LOG, "WARNING"):
                self.host.start_charge_limit()
        self.assertIsNone(self.control.conflicts)

    def test_upower_is_asked_through_the_system_bus(self):
        self.host.collector = SimpleNamespace(bus="bus")
        with patch.object(wiring, "detect_conflicts", return_value=()) as detect, \
                patch.object(wiring, "upower_thresholds_enabled", return_value=True) as check:
            self.host.start_charge_limit()
            self.assertTrue(detect.call_args.kwargs["upower"]("BAT0"))
        self.assertEqual(check.call_args.args[1], "BAT0")

    def test_snapshots_are_passed_along(self):
        self.host.observe_charge_limit()
        self.assertEqual(self.control.calls, [("observe", self.host.power)])

    def test_changes_sync_the_view_and_redraw_details_when_visibility_changes(self):
        self.host.charge_limit_changed()
        self.host.charge_limit_changed()
        self.assertEqual(self.host.charge_view.synced, 2)
        self.assertEqual(self.host.power_page.redraws, 1)
        self.control.state = ChargeState()
        self.host.charge_limit_changed()
        self.assertEqual(self.host.power_page.redraws, 2)

    def test_settings_window_follows_component_changes(self):
        calls = []
        self.host.settings_window = SimpleNamespace(admin_changed=lambda: calls.append(1))
        self.host.charge_limit_changed()
        self.host.charge_limit_changed()
        self.assertEqual(calls, [1])

    def test_conflicts_are_checked_again_after_a_write(self):
        with patch.object(wiring, "detect_conflicts", return_value=()) as detect:
            self.host.charge_limit_changed()
            self.control.state = ChargeState(supply="BAT0", layout=LAYOUTS[0], protocol=True,
                                             outcome="applied")
            self.host.charge_limit_changed()
            self.host.charge_limit_changed()
        self.assertEqual(detect.call_count, 1)

    def test_stop(self):
        self.host.stop_charge_limit()
        self.assertEqual(self.control.calls, ["stop"])
        Host().stop_charge_limit()


if __name__ == "__main__":
    unittest.main()
