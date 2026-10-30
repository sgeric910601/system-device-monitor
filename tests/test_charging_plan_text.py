"""智慧充電的文字（sdm.ui.charging_plan_text）與兩則通知（sdm.core.alert_messages）：
電源頁的狀態列、排程摘要、建立前的預估、喚醒與無人授權的說明。時間一律用台北時區。"""
from dataclasses import replace
import unittest

from sdm.core import alert_messages as messages
from sdm.core.charging_plan import (CHARGING, LATE, PRECISE_WAKE, READINESS_FIRST, READY,
                                    STARTING, SYSTEM_PAUSED, UNPLUGGED, UNSUPPORTED, WAITING,
                                    Decision, Once, Weekly, forecast, make_plan)
from sdm.services.unattended_control import UnattendedState
from sdm.services.wake_control import (FAILED, MISSING, NOT_GRANTED, OFF, SCHEDULED, WakeState)
from sdm.ui import charging_plan_text as text
from tests.test_charging_plan import TAIPEI, date

NOW = date("2026-10-13 21:00")
DEADLINE = date("2026-10-14 08:00")
PLAN = make_plan(Once(DEADLINE), DEADLINE, 100, 80, 90, PRECISE_WAKE, NOW)


def decision(phase, start="2026-10-14 05:57", required=108):
    return Decision(phase, 100, DEADLINE, date(start), None, 100, required)


class Times(unittest.TestCase):
    def test_clock_today_tomorrow_and_later(self):
        self.assertEqual(text.clock_text(date("2026-10-13 23:15"), NOW, TAIPEI), "23:15")
        self.assertEqual(text.clock_text(DEADLINE, NOW, TAIPEI), "明天 08:00")
        self.assertEqual(text.clock_text(date("2026-10-16 07:30"), NOW, TAIPEI), "10/16 07:30")

    def test_durations(self):
        self.assertEqual([text.duration_text(value) for value in (0, 45, 60, 108, 1440)],
                         ["1 分鐘", "45 分鐘", "1 小時", "1 小時 48 分", "24 小時"])


class StatusLine(unittest.TestCase):
    def line(self, plan, phase=None, now=NOW, reachable=None, **options):
        found = None if phase is None else decision(phase, **options)
        return text.status_line(plan, found, now, TAIPEI, reachable)

    def test_every_phase(self):
        cases = ((WAITING, {}, "等待開始・明天 05:57 開始", text.TONE_MUTED),
                 (STARTING, {}, "準備充電・明天 08:00 前充到 100%", text.TONE_MUTED),
                 (CHARGING, {"required": 52}, "充電中・預計 21:52 到 100%", text.TONE_GOOD),
                 (SYSTEM_PAUSED, {}, "暫停（系統）", text.TONE_WARNING),
                 (READY, {}, "已準備好", text.TONE_GOOD),
                 (UNPLUGGED, {}, "等待接上電源・明天 08:00 前充到 100%", text.TONE_MUTED),
                 (UNSUPPORTED, {}, "這顆電池無法調整充電上限", text.TONE_WARNING))
        for phase, options, expected, tone in cases:
            with self.subTest(phase=phase):
                self.assertEqual(self.line(PLAN, phase, **options), (expected, tone))

    def test_late_shows_what_can_still_be_reached(self):
        self.assertEqual(self.line(PLAN, LATE, reachable=88),
                         ("可能趕不上：只能充到 88%", text.TONE_WARNING))
        self.assertEqual(self.line(PLAN, LATE), ("可能趕不上", text.TONE_WARNING))

    def test_no_plan_restoring_and_not_yet_judged(self):
        self.assertEqual(self.line(None), (text.NO_PLAN, text.TONE_MUTED))
        self.assertEqual(self.line(replace(PLAN, restoring=True)),
                         ("還原中・改回 90%", text.TONE_MUTED))
        self.assertEqual(self.line(PLAN), (text.JUDGING, text.TONE_MUTED))


class Summary(unittest.TestCase):
    def test_once_and_weekly(self):
        self.assertEqual(text.plan_summary(PLAN, NOW, TAIPEI),
                         "明天 08:00 前充到 100%・等待時維持 80%・結束後還原為 90%")
        weekly = make_plan(Weekly(frozenset({1, 2, 3, 4, 5}), 8 * 60), DEADLINE, 90, 80, 90,
                           READINESS_FIRST, NOW)
        self.assertEqual(text.plan_summary(weekly, NOW, TAIPEI),
                         "每週一、二、三、四、五 08:00 前充到 90%（原本的上限）・等待時維持 80%")
        self.assertEqual(text.cancel_label(PLAN), "取消排程並還原為 90%")


class Forecasts(unittest.TestCase):
    def forecast_line(self, percent, target, minutes, rate=30, hold=80, plugged=True):
        deadline = NOW + minutes * 60
        result = forecast(percent, target, minutes, rate)
        return text.forecast_text(percent, target, hold, result, deadline, NOW, TAIPEI, plugged)

    def test_on_time_with_a_start_time(self):
        # 每小時 30%：65→100 要 102 分鐘；8 小時後（05:00）完成 → 加 15 分鐘緩衝後 03:03 開始
        self.assertEqual(self.forecast_line(65, 100, 8 * 60),
                         "需要約 1 小時 42 分，會在明天 03:03 開始")

    def test_starts_right_away_or_already_there(self):
        self.assertEqual(self.forecast_line(65, 100, 110), "需要約 1 小時 42 分，會立刻開始充")
        self.assertEqual(self.forecast_line(100, 100, 60), "已達 100%")
        self.assertEqual(self.forecast_line(85, 90, 600, hold=90), "會直接充到 90%（約需 18 分鐘）")

    def test_too_late_and_unplugged(self):
        self.assertEqual(self.forecast_line(65, 100, 60),
                         "來不及：最多約 88%（需要約 1 小時 42 分）")
        self.assertEqual(self.forecast_line(65, 100, 60, plugged=False),
                         "接上電源的話，來不及：最多約 88%（需要約 1 小時 42 分）")


class Notes(unittest.TestCase):
    def test_scope_in_plain_words(self):
        scope = text.unattended_scope("alice")
        for words in ("alice", "這台電腦", "正在使用", "充電上限", "喚醒", "50–100%", "隨時關閉"):
            self.assertIn(words, scope)

    def test_unattended_status(self):
        self.assertEqual(text.unattended_status(UnattendedState()), "確認中…")
        ready = UnattendedState(layout=object(), protocol=True, granted=True)
        self.assertEqual(text.unattended_status(ready), "已允許")
        self.assertEqual(text.unattended_status(replace(ready, granted=False)),
                         "未允許（需要時會要求管理員密碼）")
        self.assertIn("安裝", text.unattended_status(UnattendedState(granted=False)))
        self.assertEqual(text.unattended_status(replace(ready, busy=True)), "等待管理員授權…")

    def test_wake_notes(self):
        self.assertEqual(text.wake_note(WakeState(note=OFF), READINESS_FIRST), "")
        self.assertIn("準備優先", text.wake_note(WakeState(note=MISSING), PRECISE_WAKE))
        self.assertIn("允許無人時自動調整", text.wake_note(WakeState(note=NOT_GRANTED),
                                                    PRECISE_WAKE))
        self.assertIn("硬體時鐘", text.wake_note(WakeState(note=FAILED, error="rtc-offset"),
                                             PRECISE_WAKE))
        self.assertIn("另一個程式", text.wake_note(WakeState(note=FAILED, error="alarm-in-use"),
                                              PRECISE_WAKE))
        scheduled = WakeState(note=SCHEDULED, scheduled=int(date("2026-10-14 05:56")))
        self.assertEqual(text.wake_note(scheduled, PRECISE_WAKE, NOW, TAIPEI),
                         "已排定明天 05:56 喚醒")
        tonight = WakeState(note=SCHEDULED, scheduled=int(date("2026-10-13 23:30")))
        self.assertEqual(text.wake_note(tonight, PRECISE_WAKE, NOW, TAIPEI),
                         "已排定今天 23:30 喚醒")


class Alerts(unittest.TestCase):
    def test_ready_and_late(self):
        ready = messages.charging_ready(100)
        self.assertEqual((ready.kind, ready.title),
                         (messages.CHARGING_READY, "智慧充電：已充到 100%"))
        late = messages.charging_late(100, 88, "明天 08:00")
        self.assertEqual(late.kind, messages.CHARGING_LATE)
        self.assertIn("88%", late.body)
        self.assertIn("明天 08:00", late.body)
        self.assertEqual((ready.page, late.page), ("power", "power"))
        self.assertFalse(ready.critical or late.critical)


if __name__ == "__main__":
    unittest.main()
