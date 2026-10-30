"""智慧充電的控制器（sdm.services.plan_controller）：移植參考專案 ChargingPlanTests 中
控制器的部分（完整流程、失敗重試、使用者調整、睡眠策略、每週、過期、通知），
再加上 Linux 才有的情況：喚醒後「開機與喚醒後保留」的服務改回使用者的值、
驅動只接受部分上限、時區改變。排程只存在記憶體（PlanStore.in_memory）。
"""
from dataclasses import replace
import unittest
from zoneinfo import ZoneInfo

from sdm.core.charging_plan import (CHARGING, LATE, PRECISE_WAKE, READINESS_FIRST, READY,
                                    STARTING, SYSTEM_PAUSED, WAITING, Once, Weekly)
from sdm.services.plan_controller import (DID_WAKE, NOTE_LATE, NOTE_READY, WILL_SLEEP,
                                          PlanController)
from sdm.services.plan_store import PlanStore
from tests.test_charging_plan import TAIPEI, data, date

DEADLINE = date("2026-10-14 08:00")
EVENING = date("2026-10-13 21:00")
CHOICES = (60, 80, 100)                 # 只接受這幾個上限的驅動


class Base(unittest.TestCase):
    def setUp(self):
        self.store, self.lines = PlanStore.in_memory(), []
        self.controller = self.make()

    def make(self, **options):
        return PlanController(self.store, tz=TAIPEI, log=self.lines.append, **options)

    def start(self, schedule=Once(DEADLINE), target=100, hold=80, current=80,
              strategy=READINESS_FIRST, now=EVENING, controller=None):
        return (controller or self.controller).start(schedule, target, hold, current, strategy,
                                                     now)

    def evaluate(self, now, percent, event=None, wake_ready=False, **options):
        extra = {} if event is None else dict(event=event)
        return self.controller.evaluate(data(now, percent, **options), wake_ready=wake_ready,
                                        **extra)

    def finished(self, target, success=True, now=EVENING):
        self.controller.write_finished(target, success, target if success else None, now)

    @property
    def phase(self):
        return self.controller.decision.phase


class FullCycle(Base):
    def test_full_cycle_writes_hold_then_target_then_restores(self):
        # 原本上限 90%、電量 78%：排程 8:00 前充到 100%，等待時維持 80%
        self.assertTrue(self.start(current=90))
        self.assertEqual(self.store.load().restore, 90)
        self.assertEqual(self.evaluate(EVENING, 78, limit=90), 80)
        self.assertIsNone(self.evaluate(EVENING, 78, limit=90))      # 寫入中不重複要求
        self.finished(80)
        self.assertEqual(self.store.load().applied, 80)
        self.assertIsNone(self.evaluate(EVENING + 60, 80, limit=80))
        self.assertEqual(self.phase, WAITING)
        # 開始時間到了：提高到 100%
        start = date("2026-10-14 06:30")
        self.assertEqual(self.evaluate(start, 80, limit=80), 100)
        self.finished(100, now=start)
        self.assertEqual(self.store.load().started_for, DEADLINE)
        # 剛提高上限、還沒開始充：顯示準備充電；超過 2 分鐘還沒充才算系統暫停
        self.evaluate(start + 13, 85, limit=100, estimate=30, paused=True)
        self.assertEqual(self.phase, STARTING)
        self.evaluate(start + 150, 85, limit=100, estimate=30, paused=True)
        self.assertEqual(self.phase, SYSTEM_PAUSED)
        self.assertIsNone(self.evaluate(start + 60, 86, limit=100, estimate=30, charging=True))
        self.assertEqual(self.phase, CHARGING)
        # 充好了：通知一次；重開程式也不會再通知
        done = date("2026-10-14 07:40")
        self.assertIsNone(self.evaluate(done, 100, limit=100))
        self.assertEqual(self.controller.take_notification(), NOTE_READY)
        self.assertIsNone(self.controller.take_notification())
        reloaded = PlanController(self.store, tz=TAIPEI, log=lambda line: None)
        reloaded.evaluate(data(done + 60, 100, limit=100))
        self.assertIsNone(reloaded.take_notification())
        # 拔掉電源：還原原本的 90%，寫入成功才結束排程
        self.assertEqual(self.evaluate(date("2026-10-14 08:10"), 100, connected=False,
                                       limit=100), 90)
        self.assertTrue(self.store.load().restoring)
        self.finished(90)
        self.assertIsNone(self.controller.plan)
        self.assertIsNone(self.store.load())
        self.assertTrue(any("排程結束" in line for line in self.lines))

    def test_cancel_when_the_limit_is_already_the_original(self):
        self.start(current=80)
        self.assertIsNone(self.controller.cancel(data(EVENING, 70, limit=80)))
        self.assertIsNone(self.controller.plan)


class Retries(Base):
    def test_failed_writes_retry_after_a_minute(self):
        self.start(current=100)
        self.assertEqual(self.evaluate(EVENING, 70, limit=100), 80)
        self.finished(80, success=False)
        self.assertIsNotNone(self.controller.write_error)
        self.assertIsNone(self.evaluate(EVENING + 30, 70, limit=100))
        self.assertEqual(self.evaluate(EVENING + 61, 70, limit=100), 80)
        self.finished(80, now=EVENING + 61)
        self.assertIsNone(self.controller.write_error)
        # 還原失敗也保留排程，稍後重試
        self.assertEqual(self.controller.cancel(data(EVENING + 120, 70, limit=80)), 100)
        self.finished(100, success=False, now=EVENING + 120)
        self.assertTrue(self.controller.plan.restoring)
        self.assertIsNone(self.evaluate(EVENING + 150, 70, limit=80))
        self.assertEqual(self.evaluate(EVENING + 181, 70, limit=80), 100)
        self.finished(100, now=EVENING + 181)
        self.assertIsNone(self.controller.plan)

    def test_busy_or_rate_limited_helper_is_retried_like_any_failure(self):
        """小程式回報 busy 或 rate-limited（另一個程式剛寫過）時看板收到失敗，1 分鐘後重試。"""
        self.start(current=100)
        self.assertEqual(self.evaluate(EVENING, 70, limit=100), 80)
        self.controller.write_finished(80, False, 100, EVENING)
        for offset in (1, 20, 59):
            self.assertIsNone(self.evaluate(EVENING + offset, 70, limit=100))
        self.assertEqual(self.evaluate(EVENING + 60, 70, limit=100), 80)

    def test_results_for_another_value_are_ignored(self):
        self.start(current=100)
        self.evaluate(EVENING, 70, limit=100)
        self.controller.write_finished(95, True, 95, EVENING)
        self.assertIsNone(self.controller.plan.applied)
        self.assertIsNone(self.evaluate(EVENING + 1, 70, limit=100))     # 仍在等 80


class UserChanges(Base):
    def test_user_changes_end_the_plan_without_restoring(self):
        self.start(current=100)
        self.assertEqual(self.evaluate(EVENING, 70, limit=100), 80)
        self.finished(80)
        # 被其他程式或使用者改成 95%：結束排程，不改回去
        self.assertIsNone(self.evaluate(EVENING + 60, 70, limit=95))
        self.assertIsNone(self.controller.plan)
        self.assertIsNone(self.store.load())
        # 在電源頁拖動滑桿：同樣結束
        self.start(current=95)
        self.controller.user_changed_limit(85)
        self.assertIsNone(self.controller.plan)
        # 取代排程時沿用第一次記下的原本上限
        self.start(current=95)
        self.start(Once(date("2026-10-14 09:00")), current=80)
        self.assertEqual(self.controller.plan.restore, 95)

    def test_restore_unit_after_wake_does_not_end_the_plan(self):
        """喚醒後「開機與喚醒後保留」的服務把上限改回使用者的值：排程進行中時以排程為準。"""
        self.start(current=90)
        self.assertEqual(self.evaluate(EVENING, 70, limit=90), 80)
        self.finished(80)
        self.assertEqual(self.evaluate(EVENING + 3600, 70, DID_WAKE, limit=90), 80)
        self.assertIsNotNone(self.controller.plan)
        self.assertTrue(any("喚醒後" in line for line in self.lines))
        self.finished(80, now=EVENING + 3600)
        self.assertEqual(self.controller.plan.applied, 80)

    def test_unplugged_after_wake_keeps_the_restored_value_as_the_baseline(self):
        self.start(current=90)
        self.evaluate(EVENING, 70, limit=90)
        self.finished(80)
        self.assertIsNone(self.evaluate(EVENING + 3600, 70, DID_WAKE, connected=False, limit=90))
        self.assertIsNone(self.evaluate(EVENING + 3602, 70, connected=False, limit=90))
        self.assertIsNotNone(self.controller.plan)
        self.assertIsNone(self.evaluate(EVENING + 3604, 70, connected=False, limit=85))
        self.assertIsNone(self.controller.plan)

    def test_pending_write_is_not_a_user_change(self):
        self.start(current=90)
        self.evaluate(EVENING, 70, limit=90)
        self.finished(80)
        self.assertEqual(self.evaluate(date("2026-10-14 07:00"), 75, limit=80), 100)
        self.assertIsNone(self.evaluate(date("2026-10-14 07:00") + 1, 75, limit=95))
        self.assertIsNotNone(self.controller.plan)


class Sleep(Base):
    def test_sleep_strategies(self):
        now = date("2026-10-13 23:00")
        self.start(now=now)
        # 準備優先：睡眠前還在等待就先提高到目標
        self.assertIsNone(self.evaluate(now, 80, limit=80))
        self.assertEqual(self.evaluate(now, 80, WILL_SLEEP, limit=80), 100)
        self.finished(100, now=now)
        # 醒來後還在等待：回到保養上限，但已經充到 84%，維持在 85%，不先放電到 80%
        self.assertEqual(self.evaluate(now + 3600, 84, DID_WAKE, limit=100), 85)
        self.finished(85, now=now + 3600)
        # 精準喚醒：已經排好喚醒就維持保養上限；喚醒沒排好時退回準備優先
        self.controller.set_strategy(PRECISE_WAKE)
        self.assertIsNone(self.evaluate(now + 3700, 84, WILL_SLEEP, wake_ready=True, limit=85))
        self.assertEqual(self.evaluate(now + 3700, 84, WILL_SLEEP, wake_ready=False, limit=85),
                         100)

    def test_strategy_change_is_saved_and_logged(self):
        self.start()
        self.controller.set_strategy(PRECISE_WAKE)
        self.assertEqual(self.store.load().strategy, PRECISE_WAKE)
        self.controller.set_strategy(PRECISE_WAKE)
        self.assertEqual(sum("睡眠策略改為" in line for line in self.lines), 1)


class WeeklyAndExpiry(Base):
    def test_weekly_plan_moves_to_next_deadline(self):
        self.start(Weekly(frozenset({1, 2, 3, 4, 5}), 8 * 60))
        self.assertEqual(self.controller.plan.deadline, DEADLINE)
        self.assertEqual(self.evaluate(date("2026-10-14 06:30"), 85, limit=80), 100)
        self.finished(100, now=date("2026-10-14 06:30"))
        self.evaluate(date("2026-10-14 07:50"), 100, limit=100)
        self.assertEqual(self.controller.take_notification(), NOTE_READY)
        # 過了完成時間且已充好：換到星期四；新的一輪還沒提高過上限，不會馬上又通知
        self.evaluate(date("2026-10-14 08:05"), 100, limit=100)
        self.assertEqual(self.controller.plan.deadline, date("2026-10-15 08:00"))
        self.assertIsNone(self.controller.take_notification())
        # 每週排程拔電不會結束
        self.evaluate(date("2026-10-14 09:00"), 97, connected=False, limit=100)
        self.assertIsNotNone(self.controller.plan)
        self.assertFalse(self.controller.plan.restoring)

    def test_weekly_deadline_follows_a_timezone_change(self):
        self.start(Weekly(frozenset({3}), 8 * 60))
        self.assertEqual(self.controller.plan.deadline, DEADLINE)
        self.controller.tz = ZoneInfo("UTC")
        self.evaluate(EVENING + 60, 70, limit=80)
        self.assertEqual(self.controller.plan.deadline, DEADLINE + 8 * 3600)
        self.assertTrue(any("每週排程換到下一次" in line for line in self.lines))

    def test_once_expires_two_hours_after_deadline(self):
        self.start(current=90)
        # 95% 還要 27 分鐘，7:18 才開始；之前維持保養上限，但電量已經 95%，維持 95%、不先放電
        self.assertEqual(self.evaluate(date("2026-10-14 07:00"), 95, limit=90), 95)
        self.finished(95, now=date("2026-10-14 07:00"))
        self.assertEqual(self.evaluate(date("2026-10-14 07:30"), 95, limit=95), 100)
        self.finished(100, now=date("2026-10-14 07:30"))
        # 一直沒拔電：完成時間後 2 小時還原
        self.assertIsNone(self.evaluate(date("2026-10-14 09:30"), 100, limit=100))
        self.assertEqual(self.evaluate(date("2026-10-14 10:01"), 100, limit=100), 90)

    def test_past_once_deadline_is_refused(self):
        self.assertFalse(self.start(Once(EVENING - 60)))
        self.assertIsNone(self.controller.plan)


class Notifications(Base):
    def test_late_notification_and_quiet_ready_at_creation(self):
        # 建立時就已經達到目標：不通知
        self.start(Once(date("2026-10-13 23:00")), target=80)
        self.evaluate(EVENING, 85, limit=80)
        self.assertEqual(self.phase, READY)
        self.assertIsNone(self.controller.take_notification())
        # 一小時後要從 40% 充到 100%：可能延遲，只通知一次
        self.start(Once(EVENING + 3600))
        self.assertEqual(self.evaluate(EVENING, 40, limit=80), 100)
        self.assertEqual(self.phase, LATE)
        self.assertEqual(self.controller.take_notification(), NOTE_LATE)
        self.assertIsNone(self.controller.take_notification())

    def test_no_plan_means_no_decision(self):
        self.assertIsNone(self.evaluate(EVENING, 50))
        self.assertIsNone(self.controller.decision)
        self.assertIsNone(self.controller.take_notification())


class Options(Base):
    def test_hold_is_fitted_to_the_values_the_battery_accepts(self):
        """驅動只接受 60、80、100：等待時的 85%（不低於電量）改用 100%，還原值照原本的寫。"""
        self.start(current=67)
        self.assertEqual(self.evaluate(EVENING, 83, limit=67, options=CHOICES), 100)
        self.finished(100)
        self.assertEqual(self.controller.cancel(data(EVENING + 10, 83, limit=100,
                                                     options=CHOICES)), 67)

    def test_raising_to_a_fitted_target_counts_as_raising(self):
        """目標 90%，驅動只接受 60、80、100：提高到 100% 也算提高（寬限時間與「已準備好」通知）。"""
        self.start(target=90, hold=80)
        self.assertIsNone(self.evaluate(EVENING, 70, limit=80, options=CHOICES))
        start = date("2026-10-14 07:00")
        self.assertEqual(self.evaluate(start, 70, limit=80, options=CHOICES), 100)
        self.finished(100, now=start)
        self.assertEqual((self.controller.plan.raised_for, self.controller.plan.raised_at),
                         (DEADLINE, start))
        self.evaluate(start + 13, 75, limit=100, estimate=20, paused=True, options=CHOICES)
        self.assertEqual(self.phase, STARTING)
        self.evaluate(start + 150, 75, limit=100, estimate=20, paused=True, options=CHOICES)
        self.assertEqual(self.phase, SYSTEM_PAUSED)
        self.evaluate(start + 1800, 90, limit=100, options=CHOICES)
        self.assertEqual(self.phase, READY)
        self.assertEqual(self.controller.take_notification(), NOTE_READY)

    def test_holding_at_the_fitted_hold_is_not_a_raise(self):
        self.start(target=100, hold=85)
        self.assertEqual(self.evaluate(EVENING, 70, limit=100, options=(80, 100)), 80)
        self.finished(80)
        self.assertIsNone(self.controller.plan.raised_for)

    def test_will_sleep_raises_to_the_fitted_target(self):
        self.start(target=90, hold=80)
        self.assertIsNone(self.evaluate(EVENING, 70, limit=80, options=CHOICES))
        self.assertEqual(self.evaluate(EVENING + 60, 70, event=WILL_SLEEP, limit=80,
                                       options=CHOICES), 100)
        self.assertIn("即將睡眠，先把上限提高到 100%", self.lines[-2])

    def test_shrinking_options_refit_the_hold_and_are_logged(self):
        self.start(target=100, hold=85, current=85)
        self.assertIsNone(self.evaluate(EVENING, 70, limit=85, options=(80, 85, 90, 95, 100)))
        self.lines.clear()
        self.assertEqual(self.evaluate(EVENING + 60, 70, limit=85, options=(80, 100)), 80)
        self.assertIn("驅動接受的上限：80% 與 100%；目標的上限實際 100%、保養上限實際 80%",
                      self.lines)
        self.lines.clear()
        self.evaluate(EVENING + 120, 70, limit=85, options=(80, 100))
        self.assertFalse(any("驅動接受" in line for line in self.lines))

    def test_fitted_values_are_logged_once_for_a_new_plan(self):
        self.start(target=90, hold=80)
        self.evaluate(EVENING, 70, limit=80, options=CHOICES)
        self.assertEqual(sum("驅動接受的上限" in line for line in self.lines), 1)
        self.start(target=100, hold=80)
        self.lines.clear()
        self.evaluate(EVENING + 60, 70, limit=80, options=CHOICES)
        self.assertFalse(any("驅動接受" in line for line in self.lines))

    def test_unsupported_battery_only_reports(self):
        self.start()
        self.assertIsNone(self.evaluate(EVENING, 70, supported=False))
        self.assertEqual(self.phase, "unsupported")
        self.assertIsNotNone(self.controller.plan)

    def test_restoring_plan_from_the_store_resumes_the_restore(self):
        self.start(current=90)
        self.store.save(replace(self.controller.plan, restoring=True))
        controller = self.make()
        self.assertEqual(controller.evaluate(data(EVENING, 70, limit=80)), 90)


if __name__ == "__main__":
    unittest.main()
