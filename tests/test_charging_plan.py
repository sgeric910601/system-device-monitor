"""智慧充電的排程與判斷（sdm.core.charging_plan）：移植參考專案 ChargingPlanTests 的數值。

時間一律用 Asia/Taipei（與參考專案相同）；星期改用 ISO 編號（1＝星期一 … 7＝星期日），
參考專案的 [2, 3, 4, 5, 6]（Calendar 的星期一到五）在這裡是 [1, 2, 3, 4, 5]。
控制器的測試（完整流程、重試、使用者調整、睡眠策略、每週、過期、通知）在 test_plan_controller；
這裡是排程、所需時間、保養上限、各階段、預估、歷史充電速度、喚醒政策與今日回顧。
參考專案的 ChargingWakePolicy.keepsHelper 不移植：macOS 版的喚醒與 CPU 取樣共用同一個
管理員服務，兩者都關掉才移除；這裡的喚醒小程式是獨立的，沒有共用服務要移除。
"""
from dataclasses import replace
from datetime import datetime
import math
import unittest
from zoneinfo import ZoneInfo

from sdm.constants import SmartCharging
from sdm.core import charging_plan as engine
from sdm.core.charging_plan import (CHARGING, LATE, PRECISE_WAKE, READINESS_FIRST, READY,
                                    STARTING, SYSTEM_PAUSED, UNPLUGGED, UNSUPPORTED, WAITING,
                                    Once, PlanInput, Weekly, make_plan)
from sdm.core.history import HistorySample
from sdm.core.recap import make_recap

TAIPEI = ZoneInfo("Asia/Taipei")
HOUR = 3600


def date(text):
    """「2026-10-13 21:00」→ 台北時間的 Unix 時間。"""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI).timestamp()


def data(now, percent, connected=True, limit=80, estimate=None, charging=False, paused=False,
         supported=True, options=()):
    return PlanInput(now=now, percent=percent, connected=connected, limit_supported=supported,
                     current_limit=limit, estimated_minutes=estimate, charging=charging,
                     paused=paused, options=options)


def plan_until(deadline, strategy=READINESS_FIRST, target=100, hold=80, restore=80,
               created="2026-10-13 21:00"):
    return make_plan(Once(deadline), deadline, target, hold, restore, strategy, date(created))


class ScheduleDeadlines(unittest.TestCase):
    def test_schedule_deadlines(self):
        # 2026-10-13 是星期二
        now = date("2026-10-13 21:00")
        self.assertIsNone(Once(now - 60).next_deadline(now, TAIPEI))
        self.assertEqual(Once(date("2026-10-14 08:00")).next_deadline(now, TAIPEI),
                         date("2026-10-14 08:00"))
        # 上班日 8:00：星期二晚上的下一次是星期三早上
        workdays = Weekly(frozenset({1, 2, 3, 4, 5}), 8 * 60)
        self.assertEqual(workdays.next_deadline(now, TAIPEI), date("2026-10-14 08:00"))
        # 只選星期一：跳到下週一
        self.assertEqual(Weekly(frozenset({1}), 7 * 60 + 30).next_deadline(now, TAIPEI),
                         date("2026-10-19 07:30"))
        self.assertIsNone(Weekly(frozenset(), 480).next_deadline(now, TAIPEI))
        self.assertIsNone(Weekly(frozenset({9}), 480).next_deadline(now, TAIPEI))
        self.assertIsNone(Weekly(frozenset({1}), 24 * 60).next_deadline(now, TAIPEI))

    def test_same_weekday_after_the_time_moves_to_next_week(self):
        tuesday_morning = date("2026-10-13 08:00")
        self.assertEqual(Weekly(frozenset({2}), 8 * 60).next_deadline(tuesday_morning, TAIPEI),
                         date("2026-10-20 08:00"))
        self.assertEqual(Weekly(frozenset({2}), 8 * 60).next_deadline(tuesday_morning - 1,
                                                                      TAIPEI),
                         tuesday_morning)

    def test_skipped_daylight_saving_time_uses_the_next_existing_hour(self):
        """2026-03-08 美東 2:30 不存在（2:00 直接跳到 3:00）：與參考專案相同，改用 3:30。"""
        eastern = ZoneInfo("America/New_York")
        saturday = datetime(2026, 3, 7, 12, 0, tzinfo=eastern).timestamp()
        deadline = Weekly(frozenset({7}), 2 * 60 + 30).next_deadline(saturday, eastern)
        self.assertEqual(datetime.fromtimestamp(deadline, eastern).strftime("%m-%d %H:%M"),
                         "03-08 03:30")

    def test_repeated_hour_uses_the_first_one(self):
        eastern = ZoneInfo("America/New_York")
        saturday = datetime(2026, 10, 31, 12, 0, tzinfo=eastern).timestamp()
        deadline = Weekly(frozenset({7}), 60 + 30).next_deadline(saturday, eastern)
        moment = datetime.fromtimestamp(deadline, eastern)
        self.assertEqual((moment.hour, moment.minute, moment.fold), (1, 30, 0))
        self.assertEqual(moment.utcoffset().total_seconds(), -4 * HOUR)

    def test_bad_weekday_values_are_ignored(self):
        now = date("2026-10-13 21:00")
        self.assertIsNone(Weekly(frozenset({0, 8, True}), 480).next_deadline(now, TAIPEI))
        self.assertIsNone(Weekly(frozenset({1}), -1).next_deadline(now, TAIPEI))


class RequiredMinutes(unittest.TestCase):
    def test_required_minutes_include_the_slow_top_up(self):
        now = date("2026-10-13 21:00")
        plan = plan_until(date("2026-10-14 08:00"))
        # 沒有預估：每小時 20%，70→100 線性 90 分鐘，80% 以上的 2/3 乘 1.8 → 138 分鐘
        self.assertEqual(engine.required_minutes(plan, data(now, 70)), 138)
        # 全部在 80% 以下不加
        self.assertEqual(engine.required_minutes(replace(plan, target=80), data(now, 60)), 60)
        # 有預估用預估：80→100 的 30 分鐘全部在 80% 以上 → 54 分鐘
        self.assertEqual(engine.required_minutes(plan, data(now, 80, estimate=30)), 54)
        self.assertEqual(engine.required_minutes(plan, data(now, 100)), 0)

    def test_values_are_clamped_and_hold_never_exceeds_the_target(self):
        now = date("2026-10-13 21:00")
        odd = make_plan(Once(now), now, 120, 90, 10, READINESS_FIRST, now)
        self.assertEqual((odd.target, odd.restore), (100, 50))
        capped = make_plan(Once(now), now, 85, 95, 80, READINESS_FIRST, now)
        self.assertEqual(capped.hold, 85)

    def test_estimate_of_zero_uses_the_fallback_rate(self):
        now = date("2026-10-13 21:00")
        plan = replace(plan_until(date("2026-10-14 08:00")), target=80)
        self.assertEqual(engine.required_minutes(plan, data(now, 60, estimate=0)), 60)

    def test_never_more_than_a_day(self):
        now = date("2026-10-13 21:00")
        plan = plan_until(date("2026-10-14 08:00"))
        self.assertEqual(engine.required_minutes(plan, data(now, 0, estimate=5000)),
                         SmartCharging.MAXIMUM_MINUTES)


class HoldLimit(unittest.TestCase):
    """電量已高於保養上限時不把上限降到電量以下（電池會先放電再充回來）：
    進位到 5% 一格，不超過完成目標。"""

    def test_hold_never_below_the_current_level(self):
        deadline = date("2026-10-15 03:24")
        plan = make_plan(Once(deadline), deadline, 100, 80, 95, READINESS_FIRST,
                         date("2026-10-15 00:24"))
        self.assertEqual([engine.hold_limit(plan, value) for value in (60, 80, 81, 93)],
                         [80, 80, 85, 95])
        decision = engine.evaluate(plan, data(date("2026-10-15 00:25"), 93, limit=95))
        self.assertEqual((decision.phase, decision.desired_limit), (WAITING, 95))
        # 完成目標是原本的上限（95%）：93% 時直接維持 95%，不會降到 80%
        original = replace(plan, target=95)
        self.assertEqual(engine.hold_limit(original, 93), 95)
        self.assertEqual(engine.hold_limit(original, 98), 95)


class FittedLimits(unittest.TestCase):
    """驅動只接受部分上限時：目標向上取（一定充得到）、保養上限向下取（不高於使用者的要求），
    等待時不低於目前電量的部分也向上取；沒有選項（不限制）時照原值。"""
    OPTIONS = (60, 80, 100)

    def setUp(self):
        self.deadline = date("2026-10-14 08:00")

    def test_fit_limit_rounds_in_the_asked_direction(self):
        values = (55, 60, 61, 90, 100)
        self.assertEqual([engine.fit_limit(value, self.OPTIONS, True) for value in values],
                         [60, 60, 80, 100, 100])
        self.assertEqual([engine.fit_limit(value, self.OPTIONS, False) for value in values],
                         [60, 60, 60, 80, 100])
        self.assertEqual(engine.fit_limit(77, (), True), 77)
        self.assertEqual(engine.fit_limit(77, (), False), 77)

    def test_target_goes_up_and_hold_goes_down(self):
        plan = plan_until(self.deadline, target=90, hold=85)
        self.assertEqual((engine.target_limit(plan, self.OPTIONS),
                          engine.hold_value(plan, self.OPTIONS)), (100, 80))
        self.assertEqual((engine.target_limit(plan, ()), engine.hold_value(plan, ())), (90, 85))
        # 沒有更低的選項時只能用最小的；仍不會高於目標的上限
        low = plan_until(self.deadline, target=70, hold=55)
        self.assertEqual((engine.target_limit(low, (80, 100)), engine.hold_value(low, (80, 100))),
                         (80, 80))

    def test_waiting_limit_uses_the_fitted_values(self):
        plan = plan_until(self.deadline, target=90, hold=85)
        self.assertEqual([engine.hold_limit(plan, value, self.OPTIONS) for value in (50, 70, 83)],
                         [80, 80, 100])
        decision = engine.evaluate(plan, data(date("2026-10-13 23:00"), 70,
                                              options=self.OPTIONS))
        self.assertEqual((decision.phase, decision.desired_limit, decision.target),
                         (WAITING, 80, 90))

    def test_phases_compare_with_the_fitted_target(self):
        """目標 90%、驅動只接受 60、80、100：上限 100% 而且沒在充才是系統暫停。"""
        plan = replace(plan_until(self.deadline, target=90, hold=80), started_for=self.deadline)
        now = date("2026-10-14 07:00")
        paused = engine.evaluate(plan, data(now, 85, limit=100, estimate=20, paused=True,
                                            options=self.OPTIONS))
        self.assertEqual((paused.phase, paused.desired_limit), (SYSTEM_PAUSED, 100))
        charging = engine.evaluate(plan, data(now, 85, limit=100, estimate=20, charging=True,
                                              options=self.OPTIONS))
        self.assertEqual((charging.phase, charging.desired_limit), (CHARGING, 100))
        ready = engine.evaluate(plan, data(now, 90, limit=100, options=self.OPTIONS))
        self.assertEqual((ready.phase, ready.desired_limit), (READY, 100))

    def test_options_text(self):
        self.assertEqual(engine.options_text((80, 100)), "80% 與 100%")
        self.assertEqual(engine.options_text((60, 80, 100)), "60%、80% 與 100%")
        self.assertEqual(engine.options_text((100,)), "100%")


class EnginePhases(unittest.TestCase):
    def setUp(self):
        self.deadline = date("2026-10-14 08:00")
        self.plan = plan_until(self.deadline, PRECISE_WAKE)

    def evaluate(self, plan, now, percent, **options):
        return engine.evaluate(plan, data(now, percent, **options))

    def test_waiting_until_the_start_time(self):
        # 80→100 沒有預估：線性 60 分鐘，全部在 80% 以上 → 108 分鐘，加 15 分鐘緩衝 → 5:57 開始
        decision = self.evaluate(self.plan, date("2026-10-13 23:00"), 80)
        self.assertEqual((decision.phase, decision.desired_limit, decision.required_minutes),
                         (WAITING, 80, 108))
        self.assertEqual(decision.start_at, date("2026-10-14 05:57"))
        self.assertEqual(decision.wake_at, date("2026-10-14 05:56"))
        # 已經充好或沒接電源就不排喚醒（每週排程不會每天白白喚醒）
        self.assertIsNone(self.evaluate(self.plan, date("2026-10-13 23:00"), 100,
                                        limit=100).wake_at)
        self.assertIsNone(self.evaluate(self.plan, date("2026-10-13 23:00"), 80,
                                        connected=False).wake_at)

    def test_precise_wake_raises_one_minute_before_the_start(self):
        # 5:56 被叫醒：醒來就提高上限，不等到 5:57（被叫醒的電腦可能很快又睡回去）
        decision = self.evaluate(self.plan, date("2026-10-14 05:56") + 5, 80, limit=80)
        self.assertEqual((decision.phase, decision.desired_limit, decision.wake_at),
                         (STARTING, 100, None))
        # 準備優先照開始時間
        readiness = replace(self.plan, strategy=READINESS_FIRST)
        decision = self.evaluate(readiness, date("2026-10-14 05:56") + 5, 80, limit=80)
        self.assertEqual((decision.phase, decision.wake_at), (WAITING, None))

    def test_starting_charging_and_system_pause(self):
        decision = self.evaluate(self.plan, date("2026-10-14 06:00"), 80, limit=80)
        self.assertEqual((decision.phase, decision.desired_limit, decision.wake_at),
                         (STARTING, 100, None))
        # 充到 84%、還要 72 分鐘：重新算的開始時間（6:33）比現在晚，但這一輪已經開始，不回到等待
        options = dict(limit=100, estimate=40, charging=True)
        self.assertEqual(self.evaluate(self.plan, date("2026-10-14 06:30"), 84,
                                       **options).phase, WAITING)
        started = replace(self.plan, started_for=self.deadline)
        decision = self.evaluate(started, date("2026-10-14 06:30"), 84, **options)
        self.assertEqual((decision.phase, decision.desired_limit), (CHARGING, 100))
        # 上限已經是目標卻沒在充：系統暫停
        decision = self.evaluate(started, date("2026-10-14 06:30"), 84, limit=100, estimate=40,
                                 paused=True)
        self.assertEqual(decision.phase, SYSTEM_PAUSED)

    def test_late_ready_unplugged_and_unsupported(self):
        # 趕不上：剩 30 分鐘還要 72 分鐘
        decision = self.evaluate(self.plan, date("2026-10-14 07:30"), 90, limit=100, estimate=40,
                                 charging=True)
        self.assertEqual((decision.phase, decision.desired_limit), (LATE, 100))
        self.assertEqual(self.evaluate(self.plan, date("2026-10-14 07:50"), 100,
                                       limit=100).phase, READY)
        unplugged = self.evaluate(self.plan, date("2026-10-13 23:00"), 80, connected=False)
        self.assertEqual((unplugged.phase, unplugged.desired_limit), (UNPLUGGED, None))
        self.assertEqual(self.evaluate(self.plan, date("2026-10-13 23:00"), 80,
                                       supported=False).phase, UNSUPPORTED)

    def test_paused_before_the_limit_is_the_target_is_still_starting(self):
        started = replace(self.plan, started_for=self.deadline)
        decision = self.evaluate(started, date("2026-10-14 06:30"), 84, limit=85, estimate=40,
                                 paused=True)
        self.assertEqual(decision.phase, STARTING)


class Forecast(unittest.TestCase):
    def test_forecast_before_creating_a_plan(self):
        # 每小時 30%：65→80 要 30 分鐘，80→100 變慢（每小時 30/1.8 ≈ 16.7%）要 72 分鐘，共 102 分鐘
        forecast = engine.forecast(65, 100, 60, 30)
        self.assertEqual((forecast.needed_minutes, forecast.on_time), (102, False))
        # 一小時：前 30 分鐘到 80%，之後 30 分鐘約 +8% → 88%
        self.assertEqual(forecast.reachable_percent, 88)
        forecast = engine.forecast(65, 100, 120, 30)
        self.assertEqual((forecast.on_time, forecast.reachable_percent), (True, 100))
        # 80% 以下的目標不必算變慢
        forecast = engine.forecast(50, 80, 30, 60)
        self.assertEqual((forecast.needed_minutes, forecast.on_time), (30, True))
        # 已經在 80% 以上
        forecast = engine.forecast(85, 90, 10, 30)
        self.assertEqual((forecast.needed_minutes, forecast.reachable_percent), (18, 87))

    def test_rate_is_at_least_one_percent_per_hour(self):
        self.assertEqual(engine.forecast(99, 100, 0, 0).needed_minutes,
                         math.ceil(1 / (1 / SmartCharging.TAPER_FACTOR) * 60))


def history(start, minutes, pct, plugged=True, charging=True):
    return HistorySample(t=start + minutes * 60, pct=pct, watts=None, plugged=plugged,
                         charging=charging, ups_pct=None, ups_online=None)


class ChargingRate(unittest.TestCase):
    def test_charging_rate_from_history(self):
        start = date("2026-10-13 20:00")
        # 每 10 分鐘 +5%（每小時 30%），中間一段斷了 2 小時不算，用電池的時段不算
        samples = [history(start, index * 10, 50 + index * 5) for index in range(6)]
        samples.append(history(start, 180, 90))
        samples.append(history(start, 190, 85, plugged=False, charging=False))
        self.assertEqual(round(engine.charging_rate(samples, start + 4 * HOUR)), 30)
        self.assertIsNone(engine.charging_rate(samples[:1], start))
        # 實際的歷史是每分鐘一筆、電量是整數：每 2 分鐘 +1%（每小時 30%），不能只算電量
        # 有上升的那幾分鐘（會變成每小時 60%）
        minute = [history(start, index, 50 + index // 2) for index in range(61)]
        self.assertEqual(round(engine.charging_rate(minute, start + HOUR)), 30)
        # 太短的充電（不到 10 分鐘或不到 3%）不採用
        self.assertIsNone(engine.charging_rate(minute[:6], start + HOUR))

    def test_desktop_rows_and_old_rows_are_ignored(self):
        start = date("2026-10-13 20:00")
        desktop = [history(start, index, None) for index in range(61)]
        self.assertIsNone(engine.charging_rate(desktop, start + HOUR))
        minute = [history(start, index, 50 + index // 2) for index in range(61)]
        week_later = start + SmartCharging.RATE_WINDOW_SECONDS + 2 * HOUR
        self.assertIsNone(engine.charging_rate(minute, week_later))

    def test_median_of_sessions_and_implausible_rates(self):
        start = date("2026-10-13 00:00")
        rows = []
        for session, per_two in enumerate((1, 2, 3)):        # 每小時 30%、60%、90%
            base = session * 3 * 60
            rows += [history(start, base + index, 10 + per_two * (index // 2))
                     for index in range(31)]
        self.assertEqual(round(engine.charging_rate(rows, start + 10 * HOUR)), 60)
        fast = [history(start, index * 10, min(100, index * 30)) for index in range(4)]
        self.assertIsNone(engine.charging_rate(fast, start + HOUR))

    def test_rate_cache_keeps_the_value_for_ten_minutes(self):
        calls = []

        def samples():
            calls.append(1)
            return [history(0, index, 50 + index // 2) for index in range(61)]
        cache = engine.RateCache()
        self.assertEqual(round(cache.rate(samples, HOUR)), 30)
        cache.rate(samples, HOUR + SmartCharging.RATE_CACHE_SECONDS - 1)
        self.assertEqual(len(calls), 1)
        cache.rate(samples, HOUR + SmartCharging.RATE_CACHE_SECONDS)
        self.assertEqual(len(calls), 2)


class WakePolicy(unittest.TestCase):
    def test_wake_policy(self):
        now = 1_800_000_000
        self.assertTrue(engine.wake_allowed(now + 61, now))
        self.assertFalse(engine.wake_allowed(now + 30, now))
        self.assertFalse(engine.wake_allowed(now + 8 * 24 * HOUR, now))
        self.assertFalse(engine.wake_allowed(math.nan, now))
        self.assertFalse(engine.wake_allowed(math.inf, now))
        self.assertTrue(engine.wake_should_replace(None, now))
        self.assertFalse(engine.wake_should_replace(now, now + 120))
        self.assertTrue(engine.wake_should_replace(now, now - 600))

    def test_boundaries_and_types(self):
        now = 1_800_000_000
        self.assertTrue(engine.wake_allowed(now + 60, now))
        self.assertTrue(engine.wake_allowed(now + 7 * 24 * HOUR, now))
        self.assertFalse(engine.wake_allowed(now + 7 * 24 * HOUR + 1, now))
        for value in (None, True, "1800000061"):
            self.assertFalse(engine.wake_allowed(value, now))
        self.assertTrue(engine.wake_should_replace(now, now + 300))


class DailyRecapInTaipei(unittest.TestCase):
    """參考專案的 testDailyRecap（第 2 階段已移植到 test_recap，這裡用台北時區再驗一次）。"""

    def test_daily_recap(self):
        midnight = date("2026-10-13 00:00")

        def sample(minutes, pct, plugged):
            return history(midnight, minutes, pct, plugged, plugged)
        rows = [sample(-10, 60, True)]
        rows += [sample(m, min(80, 60 + m // 20), True) for m in range(0, 481, 10)]
        rows += [sample(490, 79, True), sample(500, 80, True)]
        rows += [sample(m, 80 - (m - 510) // 7, False) for m in range(510, 721, 10)]
        rows += [sample(730, 50, True), sample(760, 60, True)]
        rows += [sample(m, 60 - (m - 780) // 10, False) for m in range(780, 841, 10)]
        recap = make_recap(rows, midnight + 15 * HOUR, TAIPEI)
        self.assertEqual((recap.start_percent, recap.end_percent, recap.plug_in_count),
                         (60, 54, 1))
        self.assertEqual((recap.charged_percent, recap.used_percent, recap.net_change),
                         (30, 36, -6))
        self.assertEqual((recap.unplugged_minutes, recap.longest_unplugged_minutes), (280, 220))
        self.assertIsNone(make_recap([], midnight, TAIPEI).start_percent)


if __name__ == "__main__":
    unittest.main()
