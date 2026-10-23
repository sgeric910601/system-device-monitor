"""通知狀態機（純邏輯）：筆電低電量與充飽、不斷電系統、開機與喚醒後只記錄狀態、頻率限制。

數值沿用參考專案 Notifier.evaluate（低電量 ≤ 門檻通知一次、回到門檻＋2% 以上才重新啟用；
充飽要這次接電後看過充電中，拔電或低於 min(門檻, 上限) − 5% 才重新啟用）。
"""
from dataclasses import replace
import unittest

from sdm.constants import Alerts
from sdm.core import alert_messages as messages
from sdm.core.alerts import (BatteryInput, Gate, LaptopAlertState, RateLimit, UpsAlertState,
                             delivered, evaluate_laptop, evaluate_ups, failed, next_release,
                             offer, pass_gate, release)
from sdm.core.settings import Preferences
from sdm.data.ups import UpsReading

PREFS = Preferences.defaults()        # 低電量 20%、充飽 100%、不斷電系統 50%，全部開啟
DRAINING = BatteryInput(percent=50, plugged=False, draining=True, weak_charger=False,
                        charging=False, limit_reached=False, limit=None)
CHARGING = BatteryInput(percent=50, plugged=True, draining=False, weak_charger=False,
                        charging=True, limit_reached=False, limit=None)


def at(reading, percent, **changes):
    return replace(reading, percent=percent, **changes)


class LaptopRun:
    """依序送入讀值，回傳每一次的通知標題；第一次只記錄狀態（啟動）。"""

    def __init__(self, prefs=PREFS, first=None):
        self.prefs, self.state = prefs, LaptopAlertState()
        if first is not None:
            self.state, alerts = evaluate_laptop(self.state, first, prefs, record_only=True)
            assert alerts == ()

    def __call__(self, reading, record_only=False):
        self.state, alerts = evaluate_laptop(self.state, reading, self.prefs, record_only)
        return [alert.title for alert in alerts]


class LowBattery(unittest.TestCase):
    def test_fires_once_at_the_threshold_and_rearms_two_percent_above(self):
        run = LaptopRun(first=at(DRAINING, 25))
        self.assertEqual(run(at(DRAINING, 21)), [])
        self.assertEqual(run(at(DRAINING, 20)), ["電量偏低：20%"])
        self.assertEqual(run(at(DRAINING, 19)), [])
        self.assertEqual(run(at(DRAINING, 15)), [])
        self.assertEqual(run(at(DRAINING, 22)), [])        # 門檻＋2 還不算回升
        self.assertEqual(run(at(DRAINING, 20)), [])
        self.assertEqual(run(at(DRAINING, 23)), [])        # 門檻＋3：重新啟用
        self.assertEqual(run(at(DRAINING, 20)), ["電量偏低：20%"])

    def test_not_draining_rearms(self):
        run = LaptopRun(first=at(DRAINING, 30))
        self.assertEqual(run(at(DRAINING, 18)), ["電量偏低：18%"])
        self.assertEqual(run(at(CHARGING, 18)), [])
        self.assertEqual(run(at(DRAINING, 18)), ["電量偏低：18%"])

    def test_body_for_a_normal_and_a_weak_charger(self):
        state = LaptopAlertState()
        _, alerts = evaluate_laptop(state, at(DRAINING, 12), PREFS)
        self.assertEqual(alerts[0].body, "電量已降到 20% 以下，建議接上電源。")
        weak = at(DRAINING, 12, plugged=True, weak_charger=True)
        _, alerts = evaluate_laptop(state, weak, PREFS)
        self.assertEqual(alerts[0].body, "已接上電源，但充電器功率不足，電量仍在下降。")
        self.assertEqual((alerts[0].kind, alerts[0].page, alerts[0].critical),
                         (messages.LOW_BATTERY, "power", False))
        self.assertEqual(alerts[0].icon, "battery-caution-symbolic")

    def test_disabled_alert_still_records_so_enabling_later_does_not_fire(self):
        off = PREFS.with_changes(low_alert_enabled=False)
        run = LaptopRun(off, first=at(DRAINING, 40))
        self.assertEqual(run(at(DRAINING, 15)), [])
        run.prefs = PREFS
        self.assertEqual(run(at(DRAINING, 14)), [])

    def test_threshold_follows_the_setting(self):
        run = LaptopRun(PREFS.with_changes(low_threshold=10), first=at(DRAINING, 40))
        self.assertEqual(run(at(DRAINING, 15)), [])
        self.assertEqual(run(at(DRAINING, 10)), ["電量偏低：10%"])


class FullBattery(unittest.TestCase):
    def test_only_after_charging_was_seen_since_plugging_in(self):
        idle = at(CHARGING, 100, charging=False)
        run = LaptopRun(first=at(DRAINING, 100))
        self.assertEqual(run(idle), [])                    # 插上電源時已經是 100%
        run = LaptopRun(first=at(DRAINING, 90))
        self.assertEqual(run(at(CHARGING, 97)), [])
        self.assertEqual(run(idle), ["充電完成：100%"])
        self.assertEqual(run(idle), [])

    def test_unplugging_forgets_the_charging_seen_before(self):
        run = LaptopRun(first=at(CHARGING, 90))
        self.assertEqual(run(at(DRAINING, 89)), [])
        self.assertEqual(run(at(CHARGING, 100, charging=False)), [])

    def test_three_bodies(self):
        limit = at(CHARGING, 80, charging=False, limit_reached=True, limit=80)
        _, alerts = evaluate_laptop(LaptopAlertState(saw_charging=True), limit, PREFS)
        self.assertEqual(alerts[0].body, "已達充電上限 80%，電池暫停充電。")
        full = at(CHARGING, 100, charging=False)
        _, alerts = evaluate_laptop(LaptopAlertState(saw_charging=True), full, PREFS)
        self.assertEqual(alerts[0].body, "電池已充飽，可以拔掉電源。")
        self.assertEqual(alerts[0].icon, "battery-full-charged-symbolic")
        ninety = PREFS.with_changes(full_threshold=90)
        _, alerts = evaluate_laptop(LaptopAlertState(), at(CHARGING, 90), ninety)
        self.assertEqual((alerts[0].title, alerts[0].body),
                         ("充電完成：90%", "已充到 90%，可以拔掉電源。"))

    def test_rearms_on_unplug(self):
        run = LaptopRun(first=at(CHARGING, 95))
        self.assertEqual(run(at(CHARGING, 100)), ["充電完成：100%"])
        self.assertEqual(run(at(DRAINING, 99)), [])
        self.assertEqual(run(at(CHARGING, 99)), [])
        self.assertEqual(run(at(CHARGING, 100)), ["充電完成：100%"])

    def test_rearms_below_the_lower_of_threshold_and_limit_minus_five(self):
        limited = at(CHARGING, 80, limit=80)
        run = LaptopRun(first=limited)
        reached = at(limited, 80, charging=False, limit_reached=True)
        self.assertEqual(run(reached), ["充電完成：80%"])
        self.assertEqual(run(at(limited, 75, charging=False)), [])     # 80 − 5 還不算
        self.assertEqual(run(reached), [])
        self.assertEqual(run(at(limited, 74, charging=False)), [])     # 低於 75：重新啟用
        self.assertEqual(run(at(limited, 76)), [])
        self.assertEqual(run(reached), ["充電完成：80%"])

    def test_disabled_full_alert_is_silent(self):
        run = LaptopRun(PREFS.with_changes(full_alert_enabled=False), first=at(CHARGING, 90))
        self.assertEqual(run(at(CHARGING, 100)), [])


class Priming(unittest.TestCase):
    def test_start_only_records_state(self):
        state, alerts = evaluate_laptop(LaptopAlertState(), at(DRAINING, 10), PREFS,
                                        record_only=True)
        self.assertEqual((alerts, state.low_fired), ((), True))
        state, alerts = evaluate_laptop(LaptopAlertState(), at(CHARGING, 100, charging=False),
                                        PREFS, record_only=True)
        self.assertEqual((alerts, state.full_fired), ((), True))

    def test_charging_seen_while_priming_counts(self):
        run = LaptopRun(first=at(CHARGING, 90))
        self.assertEqual(run(at(CHARGING, 100, charging=False)), ["充電完成：100%"])

    def test_gate_primes_first_and_after_a_gap_longer_than_two_minutes(self):
        gate, first = pass_gate(Gate(), 1000.0)
        gate, normal = pass_gate(gate, 1000.0 + Alerts.RESUME_GAP_SECONDS)
        gate, resumed = pass_gate(gate, 1000.0 + 2 * Alerts.RESUME_GAP_SECONDS + 1)
        gate, after = pass_gate(gate, 1000.0 + 2 * Alerts.RESUME_GAP_SECONDS + 6)
        self.assertEqual((first, normal, resumed, after), (True, False, True, False))
        self.assertEqual(Alerts.RESUME_GAP_SECONDS, 120)

    def test_clock_going_backwards_primes_again(self):
        gate, _ = pass_gate(Gate(), 500.0)
        self.assertTrue(pass_gate(gate, 400.0)[1])

    def test_resume_while_low_does_not_fire(self):
        run = LaptopRun(first=at(DRAINING, 40))
        self.assertEqual(run(at(DRAINING, 12), record_only=True), [])
        self.assertEqual(run(at(DRAINING, 11)), [])


def ups(status="ONLINE", charge=100.0, load=297.0, minutes=44.0):
    if status == "COMMLOST":
        return UpsReading(status=("COMMLOST",), comm_lost=True)
    return UpsReading(status=(status,), online=status == "ONLINE",
                      on_battery=status == "ONBATT", charge_pct=charge, load_w=load,
                      time_left_min=minutes)


class UpsRun:
    def __init__(self, prefs=PREFS, first=ups()):
        self.prefs = prefs
        self.state, alerts = evaluate_ups(UpsAlertState(), first, prefs, record_only=True)
        assert alerts == ()

    def __call__(self, reading, record_only=False):
        self.state, self.alerts = evaluate_ups(self.state, reading, self.prefs, record_only)
        return [alert.title for alert in self.alerts]


class UpsAlerts(unittest.TestCase):
    def test_on_battery_needs_two_samples_and_back_on_mains_pairs_with_it(self):
        run = UpsRun()
        self.assertEqual(run(ups("ONBATT")), [])
        self.assertEqual(run(ups("ONBATT", charge=99)), ["不斷電系統改用電池供電"])
        alert = run.alerts[0]
        self.assertTrue(alert.critical)
        self.assertEqual(alert.body, "電量 99%，負載約 297 W，預估可用約 44 分鐘。")
        self.assertEqual(run(ups("ONBATT")), [])
        self.assertEqual(run(ups()), ["已恢復市電"])
        self.assertFalse(run.alerts[0].critical)
        self.assertEqual(run(ups()), [])

    def test_a_single_on_battery_sample_is_ignored(self):
        run = UpsRun()
        self.assertEqual(run(ups("ONBATT")), [])
        self.assertEqual(run(ups()), [])
        self.assertEqual(run(ups("ONBATT")), [])

    def test_unknown_load_and_time_are_left_out(self):
        run = UpsRun()
        run(ups("ONBATT", charge=None, load=None, minutes=None))
        run(ups("ONBATT", charge=None, load=None, minutes=None))
        self.assertEqual(run.alerts[0].body, "負載與可用時間暫時讀不到。")

    def test_back_on_mains_only_after_an_on_battery_notification(self):
        run = UpsRun(PREFS.with_changes(ups_alerts_enabled=False))
        run(ups("ONBATT"))
        self.assertEqual(run(ups("ONBATT")), [])
        run.prefs = PREFS
        self.assertEqual(run(ups()), [])

    def test_outage_at_start_is_not_announced_and_neither_is_its_end(self):
        run = UpsRun(first=ups("ONBATT"))
        self.assertEqual(run(ups("ONBATT")), [])
        self.assertEqual(run(ups("ONBATT")), [])
        self.assertEqual(run(ups()), [])
        run(ups("ONBATT"))
        self.assertEqual(run(ups("ONBATT")), ["不斷電系統改用電池供電"])

    def test_low_charge_fires_once_while_on_battery_and_rearms_above_threshold_plus_two(self):
        run = UpsRun()
        run(ups("ONBATT", charge=60))
        run(ups("ONBATT", charge=55))
        self.assertEqual(run(ups("ONBATT", charge=50, minutes=12)), ["不斷電系統電量偏低：50%"])
        alert = run.alerts[0]
        self.assertTrue(alert.critical)
        self.assertEqual(alert.body, "已降到你設定的 50% 以下，預估可用約 12 分鐘。請儲存工作，準備關機。")
        self.assertEqual(run(ups("ONBATT", charge=40)), [])
        self.assertEqual(run(ups(charge=52)), ["已恢復市電"])
        run(ups("ONBATT", charge=51))
        self.assertEqual(run(ups("ONBATT", charge=50)), ["不斷電系統改用電池供電"])
        self.assertEqual(run(ups(charge=53)), ["已恢復市電"])    # 門檻＋3：重新啟用
        run(ups("ONBATT", charge=51))
        self.assertEqual(run(ups("ONBATT", charge=50)),
                         ["不斷電系統改用電池供電", "不斷電系統電量偏低：50%"])

    def test_low_charge_on_mains_does_not_fire(self):
        run = UpsRun()
        self.assertEqual(run(ups(charge=30)), [])
        self.assertEqual(run(ups(charge=30)), [])

    def test_low_threshold_follows_the_setting(self):
        run = UpsRun(PREFS.with_changes(ups_low_threshold=20))
        run(ups("ONBATT", charge=40))
        self.assertEqual(run(ups("ONBATT", charge=30)), ["不斷電系統改用電池供電"])
        self.assertEqual(run(ups("ONBATT", charge=20)), ["不斷電系統電量偏低：20%"])

    def test_communication_lost_once_and_rearmed_when_it_returns(self):
        run = UpsRun()
        self.assertEqual(run(ups("COMMLOST")), ["不斷電系統通訊中斷"])
        self.assertFalse(run.alerts[0].critical)
        self.assertEqual(run(ups("COMMLOST")), [])
        self.assertEqual(run(None), [])                       # apcaccess 讀不到：不改變狀態
        self.assertEqual(run(ups("COMMLOST")), [])
        self.assertEqual(run(ups()), [])
        self.assertEqual(run(ups("COMMLOST")), ["不斷電系統通訊中斷"])

    def test_communication_lost_before_it_was_ever_seen_is_quiet(self):
        run = UpsRun(first=ups("COMMLOST"))
        self.assertEqual(run(ups("COMMLOST")), [])
        self.assertEqual(run(ups()), [])
        self.assertEqual(run(ups("COMMLOST")), ["不斷電系統通訊中斷"])

    def test_disabled_ups_alerts_are_silent(self):
        run = UpsRun(PREFS.with_changes(ups_alerts_enabled=False))
        self.assertEqual(run(ups("COMMLOST")), [])
        run(ups("ONBATT", charge=10))
        self.assertEqual(run(ups("ONBATT", charge=10)), [])


def alert(kind="a", device="", title="t"):
    return messages.Alert(kind=kind, title=title, body="", icon="x", device=device)


class RateLimiting(unittest.TestCase):
    """offer() 只決定能不能送；送出成功（delivered）才記下時間，失敗（failed）改為稍後重試。"""

    def test_one_per_kind_per_minute_and_a_burst_is_coalesced(self):
        limit, ready = offer(RateLimit(), alert(title="1"), 0.0)
        self.assertEqual([a.title for a in ready], ["1"])
        limit = delivered(limit, ready[0], 0.0)
        limit, ready = offer(limit, alert(title="2"), 10.0)
        self.assertEqual(ready, ())
        limit, ready = offer(limit, alert(title="3"), 20.0)
        self.assertEqual(ready, ())
        self.assertEqual(next_release(limit), 60.0)
        limit, ready = release(limit, 59.0)
        self.assertEqual(ready, ())
        limit, ready = release(limit, 60.0)
        self.assertEqual([a.title for a in ready], ["3"])     # 最新的取代較早的
        self.assertIsNone(next_release(limit))
        limit = delivered(limit, ready[0], 60.0)
        limit, ready = offer(limit, alert(title="4"), 100.0)
        self.assertEqual(ready, ())
        self.assertEqual(next_release(limit), 120.0)

    def test_an_alert_on_its_way_out_holds_back_the_next_one(self):
        limit, first = offer(RateLimit(), alert(title="1"), 0.0)
        limit, second = offer(limit, alert(title="2"), 0.0)
        self.assertEqual((len(first), second), (1, ()))
        self.assertIsNone(next_release(limit))                # 等第一則送完
        limit = delivered(limit, first[0], 1.0)
        self.assertEqual(next_release(limit), 61.0)

    def test_failed_send_is_retried_and_never_counts_as_sent(self):
        limit, ready = offer(RateLimit(), alert(title="1"), 0.0)
        limit = failed(limit, ready[0], 0.0)
        self.assertNotIn(("a", ""), limit.sent)
        self.assertEqual(next_release(limit), Alerts.SEND_RETRY_SECONDS)
        self.assertEqual(release(limit, 29.0)[1], ())
        limit, ready = release(limit, 30.0)
        self.assertEqual([a.title for a in ready], ["1"])
        limit = delivered(limit, ready[0], 31.0)
        self.assertEqual(limit.sent[("a", "")], 31.0)
        self.assertEqual(dict(limit.retry), {})

    def test_gives_up_after_five_attempts(self):
        limit, ready = offer(RateLimit(), alert(), 0.0)
        now = 0.0
        for attempt in range(Alerts.SEND_MAX_ATTEMPTS):
            limit = failed(limit, ready[0], now)
            now += Alerts.SEND_RETRY_SECONDS
            limit, ready = release(limit, now)
            if attempt < Alerts.SEND_MAX_ATTEMPTS - 1:
                self.assertEqual(len(ready), 1)
        self.assertEqual((ready, dict(limit.pending), dict(limit.retry), dict(limit.sent)),
                         ((), {}, {}, {}))
        self.assertEqual(Alerts.SEND_MAX_ATTEMPTS, 5)

    def test_newer_alert_waiting_survives_a_failure(self):
        limit, ready = offer(RateLimit(), alert(title="1"), 0.0)
        limit, _ = offer(limit, alert(title="2"), 0.0)
        limit = failed(limit, ready[0], 0.0)
        limit, ready = release(limit, 30.0)
        self.assertEqual([a.title for a in ready], ["2"])

    def test_kinds_and_devices_are_limited_separately(self):
        limit, _ = offer(RateLimit(), alert("a"), 0.0)
        limit, ready = offer(limit, alert("b"), 1.0)
        self.assertEqual(len(ready), 1)
        limit, ready = offer(limit, alert("p", device="1"), 2.0)
        limit, other = offer(limit, alert("p", device="2"), 3.0)
        self.assertEqual((len(ready), len(other)), (1, 1))

    def test_after_the_window_a_new_alert_goes_out_at_once(self):
        limit, ready = offer(RateLimit(), alert(), 0.0)
        limit = delivered(limit, ready[0], 0.0)
        limit, ready = offer(limit, alert(title="later"), Alerts.RATE_LIMIT_SECONDS)
        self.assertEqual([a.title for a in ready], ["later"])
        self.assertEqual(Alerts.RATE_LIMIT_SECONDS, 60)

    def test_old_entries_are_forgotten(self):
        limit, ready = offer(RateLimit(), alert("a"), 0.0)
        limit = delivered(limit, ready[0], 0.0)
        limit, ready = offer(limit, alert("b"), 500.0)
        limit = delivered(limit, ready[0], 500.0)
        self.assertEqual(set(limit.sent), {("b", "")})


if __name__ == "__main__":
    unittest.main()
