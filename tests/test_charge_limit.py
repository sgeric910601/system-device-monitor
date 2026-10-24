"""充電上限滑桿的純邏輯：移植參考專案 ChargeLimitTests 的數值，加上拖動的送出規則。"""
import math
import unittest

from sdm.constants import ChargeLimit
from sdm.core.charge_limit import (DEFAULT_OPTIONS, CommitDebouncer, index_of, limit_at,
                                   nearest, normalized, options_from, refine_options)


class Options(unittest.TestCase):
    def test_options_from_the_driver(self):
        self.assertEqual(options_from([80, 85, 90, 95, 100]), (80, 85, 90, 95, 100))
        # 去重、排序、丟掉不合理的值
        self.assertEqual(options_from([100, 90, 90, 80, 0, 255, "x"]), (80, 90, 100))
        # 沒有值、只有一個值、或沒有 100（不限制）就用預設級距
        for raw in (None, [100], [80, 90], []):
            with self.subTest(raw=raw):
                self.assertEqual(options_from(raw), DEFAULT_OPTIONS)

    def test_only_real_integers_count(self):
        self.assertEqual(options_from([True, 90.0, 60, 100]), (60, 100))
        self.assertEqual(options_from([49, 101, 60, 100]), (60, 100))

    def test_default_options_are_the_linux_steps(self):
        self.assertEqual(DEFAULT_OPTIONS, (80, 85, 90, 95, 100))
        self.assertEqual(DEFAULT_OPTIONS, ChargeLimit.OPTIONS)

    def test_normalized_limit(self):
        for raw, expected in ((95, 95), (100, 100), (50, 50), (0, 100), (255, 100), (49, 100),
                              (None, 100)):
            with self.subTest(raw=raw):
                self.assertEqual(normalized(raw), expected)


class SliderPositions(unittest.TestCase):
    def test_reference_values(self):
        options = DEFAULT_OPTIONS
        self.assertEqual(index_of(80, options), 0)
        self.assertEqual(index_of(95, options), 3)
        # 不在級距上的值放到最接近的格子
        self.assertEqual(index_of(92, options), 2)
        self.assertEqual(limit_at(3.4, options), 95)
        self.assertEqual(limit_at(-2, options), 80)
        self.assertEqual(limit_at(9, options), 100)
        self.assertIsNone(limit_at(math.nan, options))
        self.assertIsNone(limit_at(1, ()))

    def test_half_steps_round_away_from_zero(self):
        self.assertEqual(limit_at(2.5, DEFAULT_OPTIONS), 95)
        self.assertEqual(limit_at(0.5, DEFAULT_OPTIONS), 85)
        self.assertIsNone(limit_at(math.inf, DEFAULT_OPTIONS))

    def test_ties_pick_the_lower_step_and_empty_options_give_zero(self):
        self.assertEqual(index_of(87.5, DEFAULT_OPTIONS), 1)
        self.assertEqual(index_of(90, ()), 0)

    def test_nearest(self):
        self.assertEqual(nearest(92, DEFAULT_OPTIONS), 90)
        self.assertEqual(nearest(70, (60, 80, 100)), 60)
        self.assertEqual(nearest(85, (60, 80, 100)), 80)


class Refusal(unittest.TestCase):
    def test_refused_value_is_replaced_by_what_the_driver_applied(self):
        self.assertEqual(refine_options(DEFAULT_OPTIONS, 85, 80), (80, 90, 95, 100))
        self.assertEqual(refine_options((80, 90, 100), 90, 60), (60, 80, 100))

    def test_unlimited_is_never_removed(self):
        self.assertEqual(refine_options((80, 100), 100, 80), (80, 100))

    def test_unusable_reading_keeps_a_valid_list(self):
        self.assertEqual(refine_options((80, 100), 80, None), DEFAULT_OPTIONS)
        self.assertEqual(refine_options(DEFAULT_OPTIONS, 90, 0), (80, 85, 95, 100))


class FakeTimers:
    """假的計時器：advance() 依時間順序觸發到期的回呼。"""

    def __init__(self):
        self.now, self.pending, self.next_id = 0.0, {}, 1

    def schedule(self, seconds, callback):
        source = self.next_id
        self.next_id += 1
        self.pending[source] = (self.now + seconds, callback)
        return source

    def cancel(self, source):
        self.pending.pop(source, None)

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = sorted((when, source) for source, (when, _) in self.pending.items()
                         if when <= end)
            if not due:
                break
            when, source = due[0]
            self.now = when
            _, callback = self.pending.pop(source)
            callback()
        self.now = end


class Debouncer(unittest.TestCase):
    def setUp(self):
        self.timers = FakeTimers()
        self.commits = []
        self.debouncer = CommitDebouncer(self.timers.schedule, self.timers.cancel,
                                         self.commits.append)

    def test_quick_changes_commit_once_with_the_last_value(self):
        self.debouncer.drag(85)
        self.timers.advance(0.3)
        self.debouncer.drag(90)
        self.timers.advance(0.59)
        self.assertEqual(self.commits, [])
        self.timers.advance(0.02)
        self.assertEqual(self.commits, [90])
        self.timers.advance(5)
        self.assertEqual(self.commits, [90])

    def test_delay_is_six_tenths_of_a_second(self):
        self.assertEqual(ChargeLimit.DEBOUNCE, 0.6)
        self.debouncer.drag(85)
        self.timers.advance(0.6)
        self.assertEqual(self.commits, [85])

    def test_release_commits_immediately(self):
        self.debouncer.set_editing(True)
        self.debouncer.drag(85)
        self.timers.advance(2)
        self.assertEqual(self.commits, [])            # 按住滑桿時不送出
        self.debouncer.set_editing(False)
        self.assertEqual(self.commits, [85])
        self.timers.advance(2)
        self.assertEqual(self.commits, [85])

    def test_same_value_does_not_restart_the_wait(self):
        self.debouncer.drag(85)
        self.timers.advance(0.5)
        self.debouncer.drag(85)
        self.timers.advance(0.1)
        self.assertEqual(self.commits, [85])

    def test_draft_stays_until_the_write_finishes(self):
        self.debouncer.drag(85)
        self.timers.advance(1)
        self.assertEqual(self.debouncer.draft, 85)
        self.assertTrue(self.debouncer.busy)
        self.debouncer.finished(85)
        self.assertIsNone(self.debouncer.draft)
        self.assertFalse(self.debouncer.busy)

    def test_moving_during_a_write_commits_again_afterwards(self):
        self.debouncer.drag(85)
        self.timers.advance(1)
        self.debouncer.drag(95)
        self.timers.advance(1)
        self.assertEqual(self.commits, [85])          # 寫入中不送出
        self.debouncer.finished(85)
        self.assertEqual(self.commits, [85, 95])

    def test_clear_drops_the_draft(self):
        self.debouncer.drag(85)
        self.debouncer.clear()
        self.timers.advance(1)
        self.assertEqual(self.commits, [])
        self.assertIsNone(self.debouncer.draft)

    def test_release_without_a_draft_does_nothing(self):
        self.debouncer.set_editing(True)
        self.debouncer.set_editing(False)
        self.assertEqual(self.commits, [])

    def test_stop_cancels_the_timer(self):
        self.debouncer.drag(85)
        self.debouncer.stop()
        self.timers.advance(1)
        self.assertEqual(self.commits, [])


if __name__ == "__main__":
    unittest.main()
