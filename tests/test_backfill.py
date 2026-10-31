"""由 UPower 電量紀錄補回空白時段的純邏輯：點的驗證、空白的判斷、只補空白、標記、上限與檔案格式。"""
import dataclasses
import json
import math
import random
import unittest
from unittest.mock import patch

from sdm.constants import Backfill, History
from sdm.core.backfill import (SERIES_BATTERY, SERIES_UPS, Gap, Imported, InvalidImported, Request,
                               Span, UpowerPoint, accept, checked_state, decode_imported, handled,
                               imported_section, keep_in_gaps, kept_series, matches,
                               merge_imported, needs_plan, own_gaps, parse_points, plan_backfill,
                               point_sample, span_index, trim_imported)
from sdm.core.history import HistorySample, decode_history, encode_history

NOW = 1_800_000_000
DAY = 24 * 3600
CUTOFF = NOW - History.RETENTION_SECONDS
GAP = Backfill.MIN_GAP_SECONDS
EDGE = Backfill.EDGE_SECONDS


def own(t, pct=None, ups=None, plugged=True):
    return HistorySample(t, pct, 10.0, plugged, False, ups, None if ups is None else True)


def laptop(*times):
    return tuple(own(t, pct=80, plugged=False) for t in times)


def desktop(*times):
    return tuple(own(t, ups=100.0) for t in times)


def every_minute(start, end):
    return tuple(range(int(start), int(end) + 1, 60))


def imported_battery(t, pct=50, plugged=False):
    return HistorySample(t, pct, None, plugged, False, None, None, imported=True)


def imported_ups(t, value=90.0, online=True):
    return HistorySample(t, None, None, online, False, value, online, imported=True)


class ParsePoints(unittest.TestCase):
    def test_newest_first_reply_is_sorted_oldest_first(self):
        raw = [(NOW - 60, 96.0, 1), (NOW - 600, 95.0, 2)]
        self.assertEqual(parse_points(raw, NOW),
                         (UpowerPoint(NOW - 600, 95.0, 2), UpowerPoint(NOW - 60, 96.0, 1)))

    def test_unknown_zero_boundary_artefact_is_dropped(self):
        raw = [(NOW - 100, 0.0, 0), (NOW - 100, 68.0, 2), (NOW - 50, 0.0, 0)]
        self.assertEqual(parse_points(raw, NOW), (UpowerPoint(NOW - 100, 68.0, 2),))

    def test_unknown_state_with_a_real_value_is_kept(self):
        self.assertEqual(parse_points([(NOW - 100, 97.0, 0)], NOW),
                         (UpowerPoint(NOW - 100, 97.0, 0),))

    def test_invalid_entries_are_dropped(self):
        good = (NOW - 100, 50.0, 2)
        replacements = {0: (True, 0, -5, 1.5, "1", None, math.nan),
                        1: (math.nan, math.inf, -0.1, 100.1, "50", True, None),
                        2: (-1, 7, "1", True, 1.5, None)}
        for index, values in replacements.items():
            for value in values:
                with self.subTest(index=index, value=value):
                    entry = list(good)
                    entry[index] = value
                    self.assertEqual(parse_points([tuple(entry)], NOW), ())
        for entry in ((), good[:2], good + (1,), "point", None, {"t": NOW}):
            with self.subTest(entry=entry):
                self.assertEqual(parse_points([entry], NOW), ())

    def test_points_outside_the_retention_or_in_the_future_are_dropped(self):
        tolerance = History.FUTURE_TOLERANCE_SECONDS
        raw = [(CUTOFF - 1, 50.0, 2), (CUTOFF, 51.0, 2), (NOW + tolerance, 52.0, 2),
               (NOW + tolerance + 1, 53.0, 2)]
        self.assertEqual([point.t for point in parse_points(raw, NOW)],
                         [CUTOFF, NOW + tolerance])

    def test_times_are_strictly_increasing(self):
        raw = [(NOW - 100, 50.0, 2), (NOW - 100, 51.0, 2), (NOW - 200, 52.0, 2)]
        times = [point.t for point in parse_points(raw, NOW)]
        self.assertEqual(times, [NOW - 200, NOW - 100])

    def test_reply_that_is_not_a_list_gives_nothing(self):
        for raw in (None, "points", 5, {"a": 1}):
            with self.subTest(raw=raw):
                self.assertEqual(parse_points(raw, NOW), ())

    def test_number_of_points_is_capped_keeping_the_newest(self):
        count = Backfill.MAX_POINTS + 5
        raw = [(NOW - index, 50.0, 2) for index in range(count)]
        points = parse_points(raw, NOW)
        self.assertEqual(len(points), Backfill.MAX_POINTS)
        self.assertEqual(points[-1].t, NOW)
        self.assertEqual(points[0].t, NOW - Backfill.MAX_POINTS + 1)

    def test_point_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            UpowerPoint(NOW, 1.0, 1).value = 2.0


class PointSamples(unittest.TestCase):
    def test_battery_states(self):
        cases = {1: (True, True), 2: (False, False), 4: (True, False), 5: (True, False)}
        for state, (plugged, charging) in cases.items():
            with self.subTest(state=state):
                sample = point_sample(SERIES_BATTERY, UpowerPoint(NOW - 60, 96.5, state), NOW)
                self.assertEqual(sample, HistorySample(NOW - 60, 97, None, plugged, charging,
                                                       None, None, imported=True))

    def test_battery_without_a_known_plug_state_is_not_used(self):
        for state in (0, 3, 6):
            with self.subTest(state=state):
                self.assertIsNone(point_sample(SERIES_BATTERY, UpowerPoint(NOW, 50.0, state),
                                               NOW))

    def test_battery_percent_is_rounded_half_up(self):
        sample = point_sample(SERIES_BATTERY, UpowerPoint(NOW, 96.4, 2), NOW)
        self.assertEqual(sample.pct, 96)
        self.assertIs(type(sample.pct), int)

    def test_ups_states(self):
        cases = {1: True, 4: True, 5: True, 2: False, 0: None, 3: None, 6: None}
        for state, online in cases.items():
            with self.subTest(state=state):
                sample = point_sample(SERIES_UPS, UpowerPoint(NOW - 60, 96.44, state), NOW)
                self.assertEqual(sample, HistorySample(NOW - 60, None, None,
                                                       online is not False, False, 96.4, online,
                                                       imported=True))

    def test_unknown_series_is_not_used(self):
        self.assertIsNone(point_sample("watts", UpowerPoint(NOW, 50.0, 2), NOW))


class Gaps(unittest.TestCase):
    def test_time_before_the_first_own_sample(self):
        self.assertEqual(own_gaps(laptop(NOW - 3600, NOW - 3540), NOW),
                         (Gap(CUTOFF, NOW - 3600),))

    def test_no_gap_when_the_week_is_covered(self):
        samples = laptop(*every_minute(CUTOFF + 30, NOW))
        self.assertEqual(own_gaps(samples, NOW), ())

    def test_interior_gap_must_be_longer_than_the_threshold(self):
        start = CUTOFF + 30
        self.assertEqual(own_gaps(laptop(start, start + GAP), NOW), ())
        self.assertEqual(own_gaps(laptop(start, start + GAP + 1), NOW),
                         (Gap(start, start + GAP + 1),))

    def test_several_gaps_in_order(self):
        times = (CUTOFF + 30, NOW - 5 * 3600, NOW - 2 * 3600, NOW - 2 * 3600 + 60, NOW)
        self.assertEqual(own_gaps(laptop(*times), NOW),
                         (Gap(CUTOFF + 30, NOW - 5 * 3600), Gap(NOW - 5 * 3600, NOW - 2 * 3600),
                          Gap(NOW - 2 * 3600 + 60, NOW)))

    def test_gap_that_starts_before_the_retention_is_clipped(self):
        self.assertEqual(own_gaps(laptop(CUTOFF - 3600, CUTOFF + 3600), NOW),
                         (Gap(CUTOFF, CUTOFF + 3600),))

    def test_no_samples_no_gaps(self):
        self.assertEqual(own_gaps((), NOW), ())


class Planning(unittest.TestCase):
    def test_series_the_app_keeps(self):
        self.assertEqual(kept_series(laptop(NOW)), (SERIES_BATTERY,))
        self.assertEqual(kept_series(desktop(NOW)), (SERIES_UPS,))
        self.assertEqual(kept_series((own(NOW),)), ())
        self.assertEqual(kept_series(()), ())

    def test_laptop_plan_asks_for_the_battery_series(self):
        samples = laptop(NOW - 3 * 3600, NOW)
        plan = plan_backfill(samples, Imported(), NOW)
        gaps = (Gap(CUTOFF, NOW - 3 * 3600), Gap(NOW - 3 * 3600, NOW))
        self.assertEqual(plan.requests, (Request(SERIES_BATTERY, gaps),))
        self.assertEqual(plan.series, (SERIES_BATTERY,))
        self.assertEqual(plan.timespan, History.RETENTION_SECONDS)

    def test_timespan_reaches_back_to_the_oldest_pending_gap(self):
        samples = laptop(*every_minute(CUTOFF + 30, NOW - 4 * 3600), NOW)
        plan = plan_backfill(samples, Imported(), NOW)
        self.assertEqual(plan.requests, (Request(SERIES_BATTERY, (Gap(samples[-2].t, NOW),)),))
        self.assertEqual(plan.timespan, NOW - samples[-2].t)

    def test_gaps_already_handled_are_not_asked_again(self):
        samples = desktop(NOW - 3 * 3600, NOW)
        spans = (Span(SERIES_UPS, CUTOFF - 60, NOW - 3 * 3600),
                 Span(SERIES_UPS, NOW - 3 * 3600, NOW))
        self.assertEqual(plan_backfill(samples, Imported((), spans), NOW).requests, ())

    def test_partly_handled_gap_and_other_series_still_count(self):
        samples = desktop(NOW - 3 * 3600, NOW)
        spans = (Span(SERIES_UPS, NOW - 3 * 3600, NOW - 60),
                 Span(SERIES_BATTERY, CUTOFF, NOW - 3 * 3600))
        plan = plan_backfill(samples, Imported((), spans), NOW)
        self.assertEqual(plan.requests, (Request(SERIES_UPS, (Gap(CUTOFF, NOW - 3 * 3600),
                                                              Gap(NOW - 3 * 3600, NOW))),))

    def test_skipped_series_and_unkept_series_are_not_asked(self):
        samples = laptop(NOW - 3 * 3600, NOW)
        self.assertEqual(plan_backfill(samples, Imported(), NOW, (SERIES_BATTERY,)).requests, ())
        self.assertEqual(plan_backfill((own(NOW - 3600), own(NOW)), Imported(), NOW).requests,
                         ())

    def test_clock_behind_the_newest_sample_gives_no_plan(self):
        tolerance = History.FUTURE_TOLERANCE_SECONDS
        samples = laptop(NOW - 3 * 3600, NOW + tolerance + 1)
        self.assertEqual(plan_backfill(samples, Imported(), NOW).requests, ())


class Accepting(unittest.TestCase):
    def test_only_points_inside_gaps_away_from_own_samples(self):
        gaps = (Gap(NOW - 7200, NOW - 3600),)
        points = tuple(UpowerPoint(t, 50.0, 2) for t in (
            NOW - 9000, NOW - 7200 + EDGE, NOW - 7200 + EDGE + 1, NOW - 5000,
            NOW - 3600 - EDGE - 1, NOW - 3600 - EDGE, NOW - 100))
        self.assertEqual([sample.t for sample in accept(SERIES_BATTERY, points, gaps, NOW)],
                         [NOW - 7200 + EDGE + 1, NOW - 5000, NOW - 3600 - EDGE - 1])

    def test_points_without_a_plug_state_are_left_out(self):
        gaps = (Gap(NOW - 7200, NOW - 3600),)
        points = (UpowerPoint(NOW - 6000, 50.0, 0), UpowerPoint(NOW - 5000, 49.0, 2))
        self.assertEqual([sample.pct for sample in accept(SERIES_BATTERY, points, gaps, NOW)],
                         [49])

    def test_every_accepted_sample_is_marked_as_imported(self):
        gaps = (Gap(NOW - 7200, NOW - 3600),)
        points = (UpowerPoint(NOW - 6000, 95.0, 1), UpowerPoint(NOW - 5000, 96.0, 4))
        accepted = accept(SERIES_UPS, points, gaps, NOW)
        self.assertEqual([sample.imported for sample in accepted], [True, True])
        self.assertEqual([sample.watts for sample in accepted], [None, None])

    def test_number_of_points_is_capped_keeping_the_newest(self):
        gaps = (Gap(CUTOFF, NOW - 3600),)
        count = Backfill.MAX_POINTS + 10
        points = tuple(UpowerPoint(NOW - 7200 - index, 50.0, 2) for index in range(count))
        accepted = accept(SERIES_BATTERY, tuple(reversed(points)), gaps, NOW)
        self.assertEqual(len(accepted), Backfill.MAX_POINTS)
        self.assertEqual(accepted[-1].t, NOW - 7200)


class Matching(unittest.TestCase):
    """只與自己最新一筆前後 30 分鐘內的 UPower 點比較；太舊的點不能代表現在的電量。"""
    WINDOW = Backfill.MATCH_WINDOW_SECONDS

    def test_value_near_the_latest_own_sample_must_be_close(self):
        samples = desktop(NOW - 60, NOW)
        tolerance = Backfill.MATCH_TOLERANCE_PCT
        close = (UpowerPoint(NOW - 9000, 50.0, 2), UpowerPoint(NOW - 600, 100.0 - tolerance, 4))
        far = (UpowerPoint(NOW - 600, 100.0 - tolerance - 1, 4),)
        self.assertTrue(matches(close, samples, SERIES_UPS))
        self.assertFalse(matches(far, samples, SERIES_UPS))

    def test_points_older_than_the_window_are_not_compared(self):
        samples = desktop(NOW)
        self.assertTrue(matches((UpowerPoint(NOW - self.WINDOW - 1, 10.0, 2),), samples,
                                SERIES_UPS))
        self.assertFalse(matches((UpowerPoint(NOW - self.WINDOW, 10.0, 2),), samples,
                                 SERIES_UPS))

    def test_points_after_the_latest_own_sample_are_compared_too(self):
        samples = desktop(NOW - 600)
        self.assertFalse(matches((UpowerPoint(NOW - 60, 10.0, 2),), samples, SERIES_UPS))

    def test_the_closest_point_in_time_is_compared(self):
        samples = desktop(NOW)
        points = (UpowerPoint(NOW - 1500, 10.0, 2), UpowerPoint(NOW - 60, 99.0, 4))
        self.assertTrue(matches(points, samples, SERIES_UPS))

    def test_nothing_to_compare_is_accepted(self):
        self.assertTrue(matches((), desktop(NOW), SERIES_UPS))
        self.assertTrue(matches((UpowerPoint(NOW - 60, 10.0, 2),), (own(NOW),), SERIES_UPS))


class Merging(unittest.TestCase):
    def test_samples_and_handled_gaps_are_added(self):
        gaps = (Gap(NOW - 7200, NOW - 3600),)
        first = imported_battery(NOW - 5000)
        merged = merge_imported(Imported((imported_battery(NOW - 9000),), ()), SERIES_BATTERY,
                                gaps, (first,), NOW)
        self.assertEqual([sample.t for sample in merged.samples], [NOW - 9000, NOW - 5000])
        self.assertEqual(merged.spans, (Span(SERIES_BATTERY, NOW - 7200, NOW - 3600),))

    def test_gap_without_points_is_still_recorded_as_handled(self):
        merged = merge_imported(Imported(), SERIES_UPS, (Gap(NOW - 7200, NOW - 3600),), (), NOW)
        self.assertEqual(merged, Imported((), (Span(SERIES_UPS, NOW - 7200, NOW - 3600),)))

    def test_trim_drops_old_samples_and_spans(self):
        imported = Imported((imported_battery(CUTOFF - 1), imported_battery(CUTOFF + 1)),
                            (Span(SERIES_BATTERY, CUTOFF - 900, CUTOFF - 1),
                             Span(SERIES_BATTERY, CUTOFF - 900, CUTOFF + 1)))
        trimmed = trim_imported(imported, NOW)
        self.assertEqual([sample.t for sample in trimmed.samples], [CUTOFF + 1])
        self.assertEqual(trimmed.spans, (Span(SERIES_BATTERY, CUTOFF - 900, CUTOFF + 1),))

    def test_stored_samples_and_spans_are_capped_keeping_the_newest(self):
        samples = tuple(imported_battery(NOW - 10 - index) for index in
                        range(Backfill.MAX_STORED + 3))
        spans = tuple(Span(SERIES_UPS, NOW - 100 - index, NOW - 50 - index)
                      for index in range(Backfill.MAX_SPANS + 3))
        trimmed = trim_imported(Imported(tuple(reversed(samples)), spans), NOW)
        self.assertEqual(len(trimmed.samples), Backfill.MAX_STORED)
        self.assertEqual(trimmed.samples[-1].t, NOW - 10)
        self.assertEqual(len(trimmed.spans), Backfill.MAX_SPANS)
        self.assertEqual(trimmed.spans[-1].end, NOW - 50)

    def test_imported_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            Imported().samples = (1,)


class Replanning(unittest.TestCase):
    """上一次計畫是空的，而且沒有新的空白、處理過的時段、系列與時間都沒變時，不必再算一次。"""

    def setUp(self):
        self.samples = laptop(NOW - 120, NOW - 60)
        self.imported = Imported((), (Span(SERIES_BATTERY, CUTOFF, NOW - 120),))
        self.checked = checked_state(self.samples, self.imported, NOW - 60, ())

    def needs(self, samples=None, imported=None, now=NOW, skipped=()):
        return needs_plan(self.checked, self.samples if samples is None else samples,
                          self.imported if imported is None else imported, now, skipped)

    def test_first_check_always_plans(self):
        self.assertTrue(needs_plan(None, self.samples, self.imported, NOW, ()))

    def test_new_row_without_a_gap_does_not_replan(self):
        self.assertFalse(self.needs(self.samples + laptop(NOW)))
        self.assertFalse(self.needs(self.samples + laptop(NOW - 60 + GAP)))

    def test_new_row_after_a_gap_replans(self):
        self.assertTrue(self.needs(self.samples + laptop(NOW - 60 + GAP + 1)))
        self.assertTrue(self.needs(self.samples + laptop(NOW, NOW + 2 * GAP)))

    def test_trimmed_reference_row_replans(self):
        self.assertTrue(self.needs(laptop(NOW)))

    def test_changed_ranges_series_or_skipped_replan(self):
        self.assertTrue(self.needs(imported=Imported()))
        self.assertTrue(self.needs(self.samples[:1] + desktop(NOW - 60)))
        self.assertTrue(self.needs(skipped=(SERIES_BATTERY,)))

    def test_clock_moving_far_enough_replans(self):
        self.assertFalse(self.needs(now=NOW - 60 + Backfill.REPLAN_SECONDS - 1))
        self.assertTrue(self.needs(now=NOW - 60 + Backfill.REPLAN_SECONDS))
        self.assertTrue(self.needs(now=NOW - 60 - Backfill.REPLAN_SECONDS))


class HandledIndex(unittest.TestCase):
    def test_a_later_range_with_an_earlier_start_covers_the_gap(self):
        spans = (Span(SERIES_UPS, 500, 900), Span(SERIES_UPS, 100, 1000),
                 Span(SERIES_BATTERY, 0, 5000))
        index = span_index(spans, SERIES_UPS)
        self.assertTrue(handled(Gap(200, 800), index))
        self.assertTrue(handled(Gap(100, 1000), index))
        self.assertFalse(handled(Gap(50, 800), index))
        self.assertFalse(handled(Gap(200, 1001), index))
        self.assertFalse(handled(Gap(200, 800), span_index((), SERIES_UPS)))

    def test_same_answers_as_checking_every_range(self):
        rng = random.Random(20261101)
        for case in range(300):
            spans = []
            for _ in range(rng.randrange(0, 40)):
                start = rng.randrange(1, 10_000)
                spans.append(Span(rng.choice((SERIES_BATTERY, SERIES_UPS)), start,
                                  start + rng.randrange(1, 3000)))
            index = span_index(tuple(spans), SERIES_UPS)
            for _ in range(20):
                start = rng.randrange(1, 10_000)
                gap = Gap(start, start + rng.randrange(1, 3000))
                expected = any(s.series == SERIES_UPS and s.start <= gap.start and
                               gap.end <= s.end for s in spans)
                with self.subTest(case=case, gap=gap):
                    self.assertEqual(handled(gap, index), expected)


class Attempted(unittest.TestCase):
    def test_gaps_already_read_this_session_are_not_asked_again(self):
        samples = laptop(NOW - 3 * 3600, NOW)
        attempted = frozenset({(SERIES_BATTERY, NOW)})
        plan = plan_backfill(samples, Imported(), NOW, (), attempted)
        self.assertEqual(plan.requests, (Request(SERIES_BATTERY, (Gap(CUTOFF, NOW - 3 * 3600),)),))


class Duplicates(unittest.TestCase):
    """同一系列、同一時間只留一筆，留較新的那一筆。"""

    def test_merge_keeps_the_newly_read_row(self):
        old = Imported((imported_battery(NOW - 5000, 50), imported_ups(NOW - 5000, 90.0)), ())
        merged = merge_imported(old, SERIES_BATTERY, (Gap(NOW - 7200, NOW - 3600),),
                                (imported_battery(NOW - 5000, 55),), NOW)
        self.assertEqual([(s.t, s.pct, s.ups_pct) for s in merged.samples],
                         [(NOW - 5000, 55, None), (NOW - 5000, None, 90.0)])

    def test_load_keeps_the_later_row_in_the_file(self):
        section = {"samples": [imported_battery(NOW - 5000, 50).to_row(),
                               imported_battery(NOW - 5000, 51).to_row(),
                               imported_ups(NOW - 5000, 90.0).to_row()],
                   "spans": {}}
        loaded = decode_imported(section)
        self.assertEqual(sorted((s.pct, s.ups_pct) for s in loaded.samples
                                if s.pct is not None), [(51, None)])
        self.assertEqual(len(loaded.samples), 2)


class LoadCleanup(unittest.TestCase):
    """載入時只留空白裡面的補回列：手改或過時的檔案不會讓兩種來源交錯。"""

    def test_rows_near_or_between_own_rows_are_dropped(self):
        own_rows = laptop(NOW - 7200, NOW - 7140, NOW - 3600, NOW)
        times = (CUTOFF + 100, NOW - 7200 - EDGE - 1, NOW - 7200 - EDGE, NOW - 7170,
                 NOW - 7140 + EDGE, NOW - 7140 + EDGE + 1, NOW - 3600 - EDGE - 1,
                 NOW - 3600 + 30, NOW - 2000, NOW + 100)
        imported = Imported(tuple(imported_battery(t) for t in times), ())
        kept, dropped = keep_in_gaps(imported, own_rows, NOW)
        self.assertEqual([s.t for s in kept.samples],
                         [CUTOFF + 100, NOW - 7200 - EDGE - 1, NOW - 7140 + EDGE + 1,
                          NOW - 3600 - EDGE - 1, NOW - 2000])
        self.assertEqual(dropped, 5)

    def test_ranges_are_kept(self):
        imported = Imported((), (Span(SERIES_BATTERY, NOW - 7200, NOW - 3600),))
        self.assertEqual(keep_in_gaps(imported, laptop(NOW), NOW), (imported, 0))


class TrimmedRanges(unittest.TestCase):
    """超過筆數上限而丟掉的補回列，所在的已處理時段也一起丟掉，之後可以再補一次。"""

    def test_ranges_holding_trimmed_rows_are_dropped(self):
        rows = (imported_battery(NOW - 9000), imported_ups(NOW - 8500),
                imported_battery(NOW - 8000), imported_battery(NOW - 3000))
        spans = (Span(SERIES_BATTERY, NOW - 9500, NOW - 8800),
                 Span(SERIES_BATTERY, NOW - 8800, NOW - 7000),
                 Span(SERIES_UPS, NOW - 9500, NOW - 7000),
                 Span(SERIES_BATTERY, NOW - 6000, NOW - 2000))
        with patch.object(Backfill, "MAX_STORED", 2):
            trimmed = trim_imported(Imported(rows, spans), NOW)
        self.assertEqual([s.t for s in trimmed.samples], [NOW - 8000, NOW - 3000])
        # 丟掉的是 NOW - 9000 的電池列與 NOW - 8500 的不斷電系統列：只有同系列的時段跟著丟
        self.assertEqual(trimmed.spans, (Span(SERIES_BATTERY, NOW - 8800, NOW - 7000),
                                         Span(SERIES_BATTERY, NOW - 6000, NOW - 2000)))

    def test_retention_trim_keeps_the_other_ranges(self):
        rows = (imported_battery(CUTOFF - 10), imported_battery(NOW - 3000))
        spans = (Span(SERIES_BATTERY, CUTOFF - 100, CUTOFF + 50),
                 Span(SERIES_BATTERY, NOW - 6000, NOW - 2000))
        self.assertEqual(trim_imported(Imported(rows, spans), NOW).spans, spans)


class FileSection(unittest.TestCase):
    def imported(self):
        return Imported((imported_battery(NOW - 5000, 61, True), imported_ups(NOW - 4000, 97.5)),
                        (Span(SERIES_BATTERY, NOW - 7200, NOW - 3600),
                         Span(SERIES_UPS, NOW - 7000, NOW - 3500)))

    def test_nothing_imported_writes_no_section(self):
        self.assertIsNone(imported_section(Imported()))

    def test_round_trip_through_json(self):
        section = json.loads(json.dumps(imported_section(self.imported())))
        self.assertEqual(section, {
            "samples": [[NOW - 5000, 61, None, True, False, None, None],
                        [NOW - 4000, None, None, True, False, 97.5, True]],
            "spans": {"pct": [[NOW - 7200, NOW - 3600]], "ups_pct": [[NOW - 7000, NOW - 3500]]}})
        self.assertEqual(decode_imported(section), self.imported())

    def test_missing_section_is_empty(self):
        self.assertEqual(decode_imported(None), Imported())

    def test_invalid_sections_are_rejected(self):
        row = [NOW - 5000, 61, None, True, False, None, None]
        spans = {"pct": [[NOW - 7200, NOW - 3600]]}
        cases = ([], "x", {"samples": [row]}, {"spans": spans}, {"samples": {}, "spans": spans},
                 {"samples": [row], "spans": []},
                 {"samples": [row], "spans": {"watts": [[1, 2]]}},
                 {"samples": [row], "spans": {"pct": [[2, 1]]}},
                 {"samples": [row], "spans": {"pct": [[1]]}},
                 {"samples": [row], "spans": {"pct": [[0, 1]]}},
                 {"samples": [row], "spans": {"pct": [[1, math.inf]]}},
                 {"samples": [row], "spans": {"pct": [[True, 2]]}},
                 {"samples": [row], "spans": {"pct": "x"}},
                 {"samples": [["bad"]], "spans": spans},
                 {"samples": [[NOW, 61, 5.0, True, False, None, None]], "spans": spans},
                 {"samples": [[NOW, 61, None, True, False, 90.0, True]], "spans": spans},
                 {"samples": [[NOW, None, None, True, False, None, None]], "spans": spans})
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(InvalidImported):
                    decode_imported(case)

    def test_file_with_imports_stays_readable_by_format_one_readers(self):
        mine = (own(NOW - 120, pct=70), own(NOW - 60, pct=69))
        data = encode_history(mine, imported_section(self.imported()))
        raw = json.loads(data)
        self.assertEqual(raw["format"], 1)
        self.assertEqual([len(row) for row in raw["samples"]], [7, 7])
        self.assertEqual(decode_history(data), (mine, 0))

    def test_section_holds_only_series_keys_and_rows(self):
        section = imported_section(self.imported())
        self.assertEqual(sorted(section), ["samples", "spans"])
        self.assertEqual(sorted(section["spans"]), [SERIES_BATTERY, SERIES_UPS])


if __name__ == "__main__":
    unittest.main()
