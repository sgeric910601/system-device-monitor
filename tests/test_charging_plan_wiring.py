"""看板的智慧充電接線（sdm.ui.charging_plan_wiring）：每份耗電快照判斷一次並經由充電上限寫入、
只在有排程時拿睡眠延遲鎖、精準喚醒的同步、通知、使用者調整優先、睡眠與喚醒的流程
（喚醒後等 3 秒再判斷，「開機與喚醒後保留」改回的值以排程為準）、建立與取消排程，
以及示範模式與桌機不執行任何程式、不連 logind。

不建立 GTK 元件；充電上限、喚醒、授權與睡眠延遲以假物件代替，時間以假時鐘代替。
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdm.constants import SmartCharging
from sdm.core.battery import CHARGING, DISCHARGING, PLUGGED_IDLE, BatterySnapshot
from sdm.core.charging_plan import PRECISE_WAKE, READINESS_FIRST, Once, Weekly
from sdm.core.estimates import Averages, Estimates
from sdm.services.charge_control import ChargeState
from sdm.services.plan_controller import PlanController
from sdm.services.plan_store import MemoryLog, PlanStore
from sdm.services.unattended_control import DemoUnattended, UnattendedState
from sdm.services.wake_control import DemoWake, WakeState
from sdm.ui import charging_plan_wiring as wiring
from tests.fixtures.fake_timers import FakeTimers

NOW = 1_800_000_000.0
HOUR = 3600


def snapshot(percent=70, plugged=True, state=PLUGGED_IDLE):
    battery = BatterySnapshot(percent=percent, ac_online=plugged, energy_full_wh=58.0,
                              energy_now_wh=58.0 * percent / 100)
    return SimpleNamespace(battery=battery, estimates=Estimates(state=state,
                                                                averages=Averages()), stamp=NOW)


class Charge:
    """假的充電上限：自動寫入由測試決定何時完成。"""

    def __init__(self, supply="BAT0", limit=90):
        self.state = ChargeState(supply=supply, limit=limit, protocol=True, demo=True)
        self.writes, self.observed, self.reloads, self.cancelled = [], [], 0, 0
        self.user_commit, self.saved = None, None

    def apply_automatically(self, target, done, exact=False, urgent=False):
        self.writes.append([target, exact, done, urgent])

    def skip(self, index=-1):
        """即將睡眠時來不及寫：回報沒有嘗試。"""
        self.writes[index][2](None, self.state.limit)

    def finish(self, ok=True, index=-1):
        target, _, done, _ = self.writes[index]
        if ok:
            self.state = ChargeState(supply="BAT0", limit=target, protocol=True, demo=True)
        done(ok, self.state.limit)

    def observe(self, power):
        self.observed.append(power)

    def reload(self):
        self.reloads += 1

    def cancel_automatic(self):
        self.cancelled += 1

    def saved_limit(self):
        return self.saved


class Wake:
    def __init__(self):
        self.syncs, self.cleared, self.ready_value = [], [], False
        self.state = WakeState()

    def sync(self, wake_at, enabled, granted):
        self.syncs.append((wake_at, enabled, granted))

    def ready(self):
        return self.ready_value

    def clear_now(self, done):
        self.cleared.append(done)

    def refresh(self):
        self.syncs.append("refresh")


class Unattended:
    def __init__(self, granted=True):
        self.state = UnattendedState(protocol=True, granted=granted, demo=True)
        self.refreshed, self.requests = 0, []

    def refresh(self):
        self.refreshed += 1

    def set_granted(self, wanted, done):
        self.requests.append(wanted)
        done(True)


class Gate:
    def __init__(self):
        self.active, self.started = [], 0

    def start(self):
        self.started += 1

    def set_active(self, wanted):
        self.active.append(wanted)

    def stop(self):
        self.active.append("stopped")


class View:
    def __init__(self):
        self.synced = 0

    def sync(self, snapshot):
        self.synced += 1
        self.last = snapshot


class Host(wiring.ChargingPlanMixin):
    def __init__(self, preview=None, smoke=False, supply="BAT0"):
        self.preview, self.smoke, self.closing, self.settings_window = preview, smoke, False, None
        self.charge_control = Charge(supply)
        self.power_page = SimpleNamespace(plan=None)
        self.power_page.set_charging_plan = lambda view: setattr(self.power_page, "plan", view)
        self.alerts = SimpleNamespace(sent=[])
        self.alerts.deliver = lambda alerts: self.alerts.sent.extend(alerts)
        self.history = SimpleNamespace(samples=())
        self.preferences = SimpleNamespace(charging_target_percent=100, charging_hold_percent=80,
                                           charging_sleep_strategy=READINESS_FIRST,
                                           charging_wake_enabled=False)
        self.updates = []
        self.store = SimpleNamespace(value=lambda key: getattr(self.preferences, key),
                                     update=lambda **changes: self.updates.append(changes))
        self.timers, self.wall, self.power = FakeTimers(), NOW, snapshot()
        self.schedule = self.timers.schedule
        self.remove_source = self.timers.cancel
        self.plan_clock = lambda: self.wall
        self.plan_monotonic = lambda: self.timers.now
        self.fakes = SimpleNamespace(wake=Wake(), unattended=Unattended(), gate=Gate())

    def current_power(self):
        return self.power

    def make_plan_parts(self):
        if self.preview:
            return super().make_plan_parts()
        store, log = PlanStore.in_memory(), MemoryLog(clock=lambda: self.wall)
        controller = PlanController(store, log=log.write)
        return wiring.PlanParts(controller, log, self.fakes.wake, self.fakes.unattended,
                                self.fakes.gate)

    def make_plan_view(self):
        return View()

    def advance(self, seconds):
        self.wall += seconds
        self.timers.advance(seconds)


class Base(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.host.build_charging_plan()
        self.host.start_plan_components()
        self.charge = self.host.charge_control

    @property
    def controller(self):
        return self.host.plan_controller

    def start(self, schedule=None, strategy=READINESS_FIRST):
        self.host.preferences.charging_sleep_strategy = strategy
        return self.host.start_plan(schedule or Once(NOW + 12 * HOUR))


class Building(Base):
    def test_row_goes_on_the_power_page_and_user_commits_are_watched(self):
        self.assertIsInstance(self.host.power_page.plan, View)
        self.assertIsNotNone(self.charge.user_commit)
        self.assertEqual((self.host.fakes.unattended.refreshed, self.host.fakes.gate.started),
                         (1, 1))
        self.assertIn("refresh", self.host.fakes.wake.syncs)

    def test_desktop_never_reads_the_plan_file(self):
        loads = []

        class Store(PlanStore):
            def load(self):
                loads.append(1)
                return None
        for supply, expected in ((None, []), ("BAT0", [1])):
            with self.subTest(supply=supply), \
                    patch.object(wiring.PlanStore, "in_memory",
                                 classmethod(lambda cls, plan=None: Store(memory=True))):
                loads.clear()
                host = Host(smoke=True, supply=supply)
                host.make_plan_parts = lambda host=host: wiring.ChargingPlanMixin.make_plan_parts(host)
                host.build_charging_plan()
                host.start_plan_components()
                self.assertEqual(loads, expected)

    def test_desktop_starts_nothing(self):
        host = Host(supply=None)
        host.build_charging_plan()
        host.start_plan_components()
        self.assertEqual((host.fakes.unattended.refreshed, host.fakes.gate.started), (0, 0))
        host.plan_tick()
        self.assertEqual(host.charge_control.writes, [])

    def test_preview_uses_a_demo_plan_and_never_runs_anything(self):
        host = Host(preview="laptop-charging")
        host.build_charging_plan()
        host.start_plan_components()
        self.assertIsNone(host.plan_gate)
        self.assertIsInstance(host.plan_wake, DemoWake)
        self.assertIsInstance(host.plan_unattended, DemoUnattended)
        self.assertIsNotNone(host.plan_controller.plan)
        self.assertEqual(host.plan_controller.plan.strategy, PRECISE_WAKE)

    def test_preview_never_creates_a_helper_runner_or_a_logind_gate(self):
        def refuse(*_args, **_kwargs):
            raise AssertionError("preview must not run helpers or talk to logind")
        host = Host(preview="laptop-battery")
        with patch.object(wiring, "HelperRunner", refuse), patch.object(wiring, "SleepGate",
                                                                         refuse):
            host.build_charging_plan()
            host.start_plan_components()
            host.plan_will_sleep(lambda: None)
        self.assertEqual(host.charge_control.writes[-1][1:2], [False])

    def test_smoke_test_keeps_the_plan_in_memory_without_logind(self):
        host = Host(smoke=True)
        parts = wiring.ChargingPlanMixin.make_plan_parts(host)
        self.assertIsNone(parts.gate)
        self.assertTrue(parts.controller.store.memory)


class Ticks(Base):
    def test_no_plan_releases_the_lock_and_writes_nothing(self):
        self.host.plan_tick()
        self.assertEqual(self.charge.writes, [])
        self.assertEqual(self.host.fakes.gate.active[-1], False)

    def test_plan_writes_the_hold_limit_and_takes_the_lock(self):
        self.assertTrue(self.start())
        self.assertEqual(self.charge.writes[-1][:2], [80, False])
        self.assertEqual(self.host.fakes.gate.active[-1], True)
        self.charge.finish()
        self.assertEqual(self.controller.plan.applied, 80)
        self.assertEqual(self.host.plan_view.last.plan, self.controller.plan)

    def test_snapshot_is_observed_even_when_the_window_is_hidden(self):
        self.charge.observed.clear()
        self.host.plan_tick()
        self.assertEqual(self.charge.observed, [self.host.power])

    def test_restore_uses_the_exact_original_value(self):
        self.charge.state = ChargeState(supply="BAT0", limit=67, protocol=True, demo=True)
        self.host.preferences.charging_target_percent = 100
        self.start()
        self.charge.finish()
        self.host.cancel_plan()
        self.assertEqual(self.charge.writes[-1][:2], [67, True])
        self.charge.finish()
        self.assertIsNone(self.controller.plan)
        self.assertEqual(self.host.fakes.gate.active[-1], False)

    def test_cancel_while_writing_restores_after_the_write(self):
        self.charge.state = ChargeState(supply="BAT0", limit=67, protocol=True, demo=True)
        self.start()
        self.assertEqual(self.charge.writes[-1][:2], [80, False])
        self.host.cancel_plan()
        self.assertEqual(len(self.charge.writes), 1)            # 不同時寫兩個值
        self.charge.finish()
        self.host.plan_tick()
        self.assertEqual(self.charge.writes[-1][:2], [67, True])
        self.charge.finish()
        self.assertIsNone(self.controller.plan)

    def test_original_limit_prefers_the_persisted_value(self):
        self.charge.saved = SimpleNamespace(supply="BAT0", end=85)
        self.host.preferences.charging_target_percent = 0
        self.start()
        self.assertEqual(self.controller.plan.restore, 85)
        self.assertEqual(self.controller.plan.target, 85)       # 目標 0＝原本的上限

    def test_the_slider_options_are_what_the_plan_fits_to(self):
        """驅動只接受 80% 與 100%：保養上限 85% 實際寫 80%（不高於要求）。"""
        self.charge.state = ChargeState(supply="BAT0", limit=100, options=(80, 100),
                                        protocol=True, demo=True)
        self.host.preferences.charging_hold_percent = 85
        self.start()
        self.assertEqual(self.charge.writes[-1][:2], [80, False])

    def test_target_and_hold_come_from_the_preferences(self):
        self.host.preferences.charging_target_percent = 95
        self.host.preferences.charging_hold_percent = 100
        self.start()
        self.assertEqual((self.controller.plan.target, self.controller.plan.hold), (95, 95))

    def test_ready_notification_is_delivered_once(self):
        self.start()                # 12 小時後完成：先維持 80%
        self.charge.finish()
        self.host.wall = NOW + 10.5 * HOUR
        self.host.power = snapshot(85)
        self.host.plan_tick()
        self.assertEqual(self.charge.writes[-1][:2], [100, False])
        self.charge.finish()
        self.host.power = snapshot(100)
        self.host.plan_tick()
        self.host.plan_tick()
        self.assertEqual([alert.kind for alert in self.host.alerts.sent], ["charging_ready"])


class Wakes(Base):
    def test_precise_wake_is_synced_with_the_grant(self):
        self.start(Once(NOW + 12 * HOUR), PRECISE_WAKE)
        wake_at, enabled, granted = self.host.fakes.wake.syncs[-1]
        self.assertTrue(enabled and granted)
        self.assertAlmostEqual(wake_at, self.controller.decision.wake_at)
        self.host.fakes.unattended.state = UnattendedState(protocol=True, granted=False,
                                                           demo=True)
        self.host.plan_tick()
        self.assertEqual(self.host.fakes.wake.syncs[-1][2], False)

    def test_readiness_first_never_asks_for_a_wake(self):
        self.start()
        self.assertEqual(self.host.fakes.wake.syncs[-1][1], False)

    def test_strategy_change_applies_to_the_running_plan(self):
        self.start()
        self.host.preferences.charging_sleep_strategy = PRECISE_WAKE
        self.host.plan_preferences_changed({"charging_sleep_strategy"})
        self.assertEqual(self.controller.plan.strategy, PRECISE_WAKE)
        self.assertEqual(self.host.updates[-1], {"charging_wake_enabled": True})


class UserChanges(Base):
    def test_user_commit_ends_the_plan_and_cancels_queued_writes(self):
        self.start()
        self.charge.finish()
        self.charge.user_commit(95)
        self.assertIsNone(self.controller.plan)
        self.assertEqual(self.charge.cancelled, 1)
        self.assertEqual(self.host.fakes.gate.active[-1], False)
        self.assertEqual(self.host.fakes.wake.syncs[-1][1], False)


class SleepAndWake(Base):
    def setUp(self):
        super().setUp()
        self.start()
        self.charge.finish()
        self.done = []

    def test_will_sleep_raises_to_the_target_before_releasing(self):
        self.host.plan_will_sleep(lambda: self.done.append(True))
        self.assertEqual(self.charge.writes[-1][:2], [100, False])
        self.assertEqual(self.done, [])
        self.charge.finish()
        self.assertEqual(self.done, [True])

    def test_sleep_write_is_urgent_and_a_skip_releases_at_once(self):
        self.host.plan_will_sleep(lambda: self.done.append(True))
        self.assertEqual(self.charge.writes[-1][::3], [100, True])
        self.charge.skip()
        self.assertEqual(self.done, [True])
        self.assertIsNone(self.controller.write_error)
        self.assertTrue(any("來不及" in line for line in self.host.plan_log.lines))

    def test_routine_writes_are_not_urgent(self):
        self.assertFalse(self.charge.writes[-1][3])

    def test_released_before_writing_is_logged(self):
        self.host.plan_will_sleep(lambda: None)
        self.host.plan_sleep_timed_out("sleep")
        self.host.plan_sleep_timed_out("shutdown")
        lines = self.host.plan_log.lines
        self.assertTrue(any("睡眠前 4 秒內沒有寫完" in line for line in lines))
        self.assertTrue(any("關機前 4 秒內沒有取消完喚醒" in line for line in lines))

    def test_will_sleep_without_anything_to_write_releases_at_once(self):
        self.host.fakes.wake.ready_value = True
        self.host.preferences.charging_sleep_strategy = PRECISE_WAKE
        self.host.plan_preferences_changed({"charging_sleep_strategy"})
        self.host.plan_will_sleep(lambda: self.done.append(True))
        self.assertEqual(self.done, [True])

    def test_no_routine_ticks_between_sleep_and_three_seconds_after_waking(self):
        self.host.plan_will_sleep(lambda: self.done.append(True))
        self.charge.finish()
        writes = len(self.charge.writes)
        self.host.plan_tick()
        self.host.plan_did_wake()
        self.host.plan_tick()
        self.assertEqual(len(self.charge.writes), writes)
        self.assertEqual(self.charge.reloads, 0)
        self.host.advance(SmartCharging.RESUME_SETTLE_SECONDS)
        self.assertEqual(self.charge.reloads, 1)

    def test_late_resume_timer_keeps_ticks_paused_until_it_runs(self):
        """喚醒 3 秒的計時器晚執行（主迴圈忙）時，在它執行之前的一般判斷仍然暫停。"""
        timers = []
        self.host.schedule = lambda seconds, callback: timers.append(callback)
        writes = len(self.charge.writes)
        self.host.plan_did_wake()
        self.host.timers.now += SmartCharging.RESUME_SETTLE_SECONDS + 5
        self.charge.state = ChargeState(supply="BAT0", limit=90, protocol=True, demo=True)
        self.host.plan_tick()
        self.assertEqual((len(self.charge.writes), self.charge.reloads), (writes, 0))
        self.assertIsNotNone(self.controller.plan)
        timers[-1]()
        self.assertEqual(self.charge.reloads, 1)
        self.assertIsNotNone(self.controller.plan)
        self.assertEqual(self.charge.writes[-1][:2], [80, False])

    def test_restore_unit_value_after_waking_is_replaced_by_the_plan(self):
        self.charge.state = ChargeState(supply="BAT0", limit=90, protocol=True, demo=True)
        self.host.plan_did_wake()
        self.host.advance(SmartCharging.RESUME_SETTLE_SECONDS)
        self.assertIsNotNone(self.controller.plan)
        self.assertEqual(self.charge.writes[-1][:2], [80, False])

    def test_missing_wake_signal_does_not_block_ticks_forever(self):
        self.host.plan_will_sleep(lambda: None)
        self.charge.finish()
        self.host.advance(SmartCharging.SLEEP_GUARD_SECONDS)
        self.charge.state = ChargeState(supply="BAT0", limit=95, protocol=True, demo=True)
        self.host.plan_tick()
        self.assertIsNone(self.controller.plan)         # 又能看出使用者的調整

    def test_shutdown_cancels_the_wake_alarm(self):
        self.host.plan_shutdown(lambda: self.done.append(True))
        self.assertEqual(len(self.host.fakes.wake.cleared), 1)


class Snapshots(Base):
    def test_snapshot_for_the_row_dialog_and_settings(self):
        self.start(Weekly(frozenset({1, 2, 3, 4, 5}), 8 * 60))
        shot = self.host.plan_snapshot()
        self.assertEqual((shot.plan, shot.decision), (self.controller.plan,
                                                      self.controller.decision))
        self.assertEqual((shot.original_limit, shot.laptop, shot.demo), (90, True, False))
        self.assertIs(shot.wake, self.host.fakes.wake.state)
        self.assertIs(shot.unattended, self.host.fakes.unattended.state)

    def test_user_name_is_looked_up_once(self):
        calls = []

        def lookup(uid):
            calls.append(uid)
            return SimpleNamespace(pw_name="alice")
        self.host.plan_user_name = None
        with patch.object(wiring.pwd, "getpwuid", lookup):
            names = [self.host.plan_snapshot().user for _ in range(3)]
        self.assertEqual((names, len(calls)), (["alice"] * 3, 1))

    def test_unchanged_snapshot_is_not_shown_again(self):
        self.host.plan_changed()
        synced = self.host.plan_view.synced
        self.host.plan_changed()
        self.host.advance(20)
        self.host.plan_changed()
        self.assertEqual(self.host.plan_view.synced, synced)
        self.host.advance(60)
        self.host.plan_changed()
        self.assertEqual(self.host.plan_view.synced, synced + 1)
        self.start()
        self.assertGreater(self.host.plan_view.synced, synced + 1)

    def test_nothing_is_built_while_no_window_shows_it(self):
        self.host.window = SimpleNamespace(get_visible=lambda: False)
        synced, built = self.host.plan_view.synced, []
        original = self.host.plan_snapshot
        self.host.plan_snapshot = lambda: built.append(1) or original()
        self.start()
        self.host.plan_tick()
        self.assertEqual((self.host.plan_view.synced, built), (synced, []))
        self.host.window = SimpleNamespace(get_visible=lambda: True)
        self.host.plan_tick()
        self.assertEqual(self.host.plan_view.synced, synced + 1)

    def test_unattended_switch(self):
        results = []
        self.host.set_unattended(True, results.append)
        self.assertEqual((self.host.fakes.unattended.requests, results), ([True], [True]))

    def test_revoking_clears_the_app_alarm_first_even_while_it_is_being_set(self):
        """關閉授權前先取消看板排定的鬧鐘（之後就要密碼了）；正在設定時也一樣，排在它後面。"""
        for state in (WakeState(scheduled=int(NOW) + 3600), WakeState(busy=True)):
            with self.subTest(state=state):
                wake, unattended, results = self.host.fakes.wake, self.host.fakes.unattended, []
                wake.state, wake.cleared, unattended.requests = state, [], []
                self.host.set_unattended(False, results.append)
                self.assertEqual((len(wake.cleared), unattended.requests), (1, []))
                wake.cleared[0]()
                self.assertEqual((unattended.requests, results), ([False], [True]))
        self.host.fakes.wake.state, self.host.fakes.wake.cleared = WakeState(), []
        self.host.set_unattended(False, lambda ok: None)
        self.assertEqual(self.host.fakes.wake.cleared, [])


class Unplugged(Base):
    def test_unplugged_phase_writes_nothing(self):
        self.host.power = snapshot(60, plugged=False, state=DISCHARGING)
        self.start()
        self.assertEqual(self.charge.writes, [])
        self.assertEqual(self.controller.decision.phase, "unplugged")

    def test_charging_rate_comes_from_history(self):
        self.host.power = snapshot(62, state=CHARGING)
        self.start()
        self.assertIsNotNone(self.controller.decision)


if __name__ == "__main__":
    unittest.main()
