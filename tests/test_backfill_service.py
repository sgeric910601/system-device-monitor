"""補回空白時段的排程（sdm.services.backfill_service）：有還沒處理的空白才讀一次、只補空白、
每段空白只處理一次、失敗時延後再試、找不到裝置或數值對不上時這次執行不再讀。"""
import unittest
from unittest.mock import patch

from sdm.constants import Backfill, History
from sdm.core.backfill import SERIES_BATTERY, SERIES_UPS, Gap, Span
from sdm.core.history import HistorySample
from sdm.services import backfill_service
from sdm.services.backfill_service import BackfillService
from sdm.services.history_service import HistoryService
from sdm.services.upower_history import FetchResult

NOW = 1_800_000_000
LOGGER = "sdm.services.backfill_service"
CUTOFF = NOW - History.RETENTION_SECONDS


class FakeReader:
    def __init__(self):
        self.requests, self.stopped = [], 0

    def fetch(self, series, timespan, done):
        self.requests.append((series, timespan, done))

    def stop(self):
        self.stopped += 1

    def answer(self, result, index=-1):
        self.requests[index][2](result)


class Base(unittest.TestCase):
    def setUp(self):
        self.now, self.mono = NOW, 0.0
        self.history = HistoryService.in_memory(clock=lambda: self.now)
        self.reader = FakeReader()
        self.changes = []
        self.service = BackfillService(self.history, self.reader,
                                       lambda: self.changes.append(True),
                                       clock=lambda: self.now, monotonic=lambda: self.mono)

    def record(self, *times, pct=80):
        for t in times:
            self.history.add(HistorySample(t, pct, 10.0, False, False, None, None))

    def battery(self, *points):
        return FetchResult(points=((SERIES_BATTERY, points),))


class Asking(Base):
    def test_nothing_recorded_asks_nothing(self):
        self.assertFalse(self.service.check())
        self.assertEqual(self.reader.requests, [])

    def test_pending_gaps_are_asked_once(self):
        self.record(NOW - 3 * 3600, NOW)
        self.assertTrue(self.service.check())
        self.assertFalse(self.service.check())
        ((series, timespan, _),) = self.reader.requests
        self.assertEqual((series, timespan), ((SERIES_BATTERY,), History.RETENTION_SECONDS))

    def test_ups_series_on_a_desktop(self):
        self.history.add(HistorySample(NOW - 3600, None, 200.0, True, False, 100.0, True))
        self.history.add(HistorySample(NOW, None, 200.0, True, False, 100.0, True))
        self.service.check()
        self.assertEqual(self.reader.requests[0][0], (SERIES_UPS,))


class Filling(Base):
    def test_only_points_inside_gaps_are_imported(self):
        self.record(NOW - 3 * 3600, NOW - 3 * 3600 + 60, NOW)
        self.service.check()
        self.reader.answer(self.battery((NOW - 3 * 3600 + 30, 79.0, 2), (NOW - 7200, 70.0, 2),
                                        (NOW - 3600, 60.0, 1), (NOW - 30, 80.0, 2)))
        imported = self.history.imported
        self.assertEqual([(s.t, s.pct, s.plugged, s.imported) for s in imported.samples],
                         [(NOW - 7200, 70, False, True), (NOW - 3600, 60, True, True)])
        self.assertEqual(imported.spans,
                         (Span(SERIES_BATTERY, CUTOFF, NOW - 3 * 3600),
                          Span(SERIES_BATTERY, NOW - 3 * 3600 + 60, NOW)))
        self.assertEqual([s.t for s in self.history.samples],
                         [NOW - 3 * 3600, NOW - 3 * 3600 + 60, NOW])
        self.assertEqual(self.changes, [True])

    def test_handled_gaps_are_never_asked_again(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.reader.answer(self.battery())
        self.assertEqual(len(self.history.imported.spans), 2)
        self.assertEqual(self.changes, [])
        self.now += 60
        self.record(self.now)
        self.assertFalse(self.service.check())
        self.assertEqual(len(self.reader.requests), 1)

    def test_new_gap_after_waking_up_is_asked_alone(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.reader.answer(self.battery())
        self.now += 2 * 3600
        self.record(self.now)
        self.assertTrue(self.service.check())
        series, timespan, _ = self.reader.requests[-1]
        self.assertEqual((series, timespan), ((SERIES_BATTERY,), 2 * 3600))
        self.reader.answer(self.battery((NOW + 3600, 70.0, 2)))
        self.assertEqual([s.t for s in self.history.imported.samples], [NOW + 3600])
        self.assertEqual(self.history.imported.spans[-1], Span(SERIES_BATTERY, NOW, self.now))

    def test_invalid_points_are_ignored(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.reader.answer(self.battery((NOW - 7200, float("nan"), 2), (NOW - 7000, 0.0, 0),
                                        (NOW - 6000, 120.0, 2), (NOW - 5000, 75.0, 2)))
        self.assertEqual([s.t for s in self.history.imported.samples], [NOW - 5000])

    def test_mismatching_values_skip_the_series_for_this_session(self):
        self.record(NOW - 3 * 3600, NOW, pct=80)
        self.service.check()
        with self.assertLogs(LOGGER, "INFO"):
            self.reader.answer(self.battery((NOW - 7200, 30.0, 2), (NOW - 600, 30.0, 2)))
        self.assertEqual(self.history.imported.samples, ())
        self.assertEqual(self.history.imported.spans, ())
        self.now += 2 * 3600
        self.record(self.now)
        self.assertFalse(self.service.check())

    def test_missing_device_skips_the_series_for_this_session(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        with self.assertLogs(LOGGER, "INFO"):
            self.reader.answer(FetchResult(missing=(SERIES_BATTERY,)))
        self.assertEqual(self.history.imported.spans, ())
        self.mono += 24 * 3600
        self.assertFalse(self.service.check())


class Replanning(Base):
    """每記錄一筆就檢查一次，但沒有新空白時不重新掃全部紀錄。"""

    def counted(self):
        calls = []
        original = backfill_service.plan_backfill

        def counting(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)
        patcher = patch.object(backfill_service, "plan_backfill", counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_second_check_without_a_new_gap_does_not_replan(self):
        calls = self.counted()
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.reader.answer(self.battery())
        self.now += 60
        self.record(self.now)
        self.assertFalse(self.service.check())
        self.assertEqual(len(calls), 2)
        self.now += 60
        self.record(self.now)
        self.assertFalse(self.service.check())
        self.assertEqual(len(calls), 2)

    def test_new_gap_or_enough_time_replans(self):
        calls = self.counted()
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.reader.answer(self.battery())
        self.service.check()
        count = len(calls)
        self.now += Backfill.REPLAN_SECONDS
        self.assertFalse(self.service.check())
        self.assertEqual(len(calls), count + 1)
        self.now += Backfill.MIN_GAP_SECONDS + 1
        self.record(self.now)
        self.assertTrue(self.service.check())
        self.assertEqual(len(calls), count + 2)

    def test_gap_read_this_session_is_not_read_again_when_its_range_was_trimmed(self):
        self.record(NOW - 3 * 3600, NOW)
        with patch.object(Backfill, "MAX_STORED", 1):
            self.service.check()
            self.reader.answer(self.battery((NOW - 7200, 70.0, 2), (NOW - 3600, 75.0, 2)))
            self.assertEqual(len(self.history.imported.samples), 1)
            self.assertNotIn(NOW, [span.end for span in self.history.imported.spans])
            self.now += Backfill.REPLAN_SECONDS
            self.assertFalse(self.service.check())
        self.assertEqual(len(self.reader.requests), 1)


class Failures(Base):
    def test_failure_retries_later_and_gives_up_after_three(self):
        self.record(NOW - 3 * 3600, NOW)
        delays = list(Backfill.RETRY_SECONDS)
        for attempt in range(Backfill.MAX_FAILURES):
            with self.subTest(attempt=attempt):
                self.assertTrue(self.service.check())
                self.reader.answer(FetchResult(error="timeout"))
                if attempt < len(delays):
                    self.mono += delays[attempt] - 1
                    self.assertFalse(self.service.check())
                    self.mono += 1
        self.assertFalse(self.service.check())
        self.mono += 365 * 24 * 3600
        self.assertFalse(self.service.check())
        self.assertEqual(len(self.reader.requests), Backfill.MAX_FAILURES)
        self.assertEqual(self.history.imported.spans, ())

    def test_success_resets_the_failure_count(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.reader.answer(FetchResult(error="timeout"))
        self.mono += Backfill.RETRY_SECONDS[0]
        self.service.check()
        self.reader.answer(self.battery())
        self.assertEqual((self.service.failures, self.service.retry_at), (0, None))

    def test_stop_cancels_and_ignores_late_results(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.service.stop()
        self.assertEqual(self.reader.stopped, 1)
        self.reader.answer(self.battery((NOW - 7200, 70.0, 2)))
        self.assertEqual(self.history.imported.samples, ())
        self.assertFalse(self.service.check())
        self.assertEqual(self.changes, [])

    def test_result_gaps_are_the_ones_that_were_asked(self):
        self.record(NOW - 3 * 3600, NOW)
        self.service.check()
        self.now += 3 * 3600
        self.record(self.now)
        self.reader.answer(self.battery((NOW + 3600, 70.0, 2)))
        self.assertEqual(self.history.imported.samples, ())
        self.assertNotIn(Gap(NOW, self.now), [Gap(s.start, s.end)
                                              for s in self.history.imported.spans])


if __name__ == "__main__":
    unittest.main()
