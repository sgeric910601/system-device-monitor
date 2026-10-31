"""歷史紀錄純邏輯：列驗證、7 天保留、圖表降採樣與分段、拔電時間。

參考專案 HistoryTests.testUnpluggedTimeStartsAtBootAfterShutdown 全部案例以相同數字移植
（LastUnplugged 類別）。參考專案的 pmset 回填只適用 macOS，不移植；Linux 改由 UPower
自己的電量紀錄補回空白時段（見 test_backfill）。
"""
import dataclasses
import json
import math
import random
import unittest

from sdm.constants import History
from sdm.core.history import (HistorySample, NewerFormat, Undecodable, chart_points,
                              clamp_to_boot, decode_history, encode_history, last_unplugged,
                              parse_boot_time, sample_from_row, trim, valid_sample)

NOW = 1_800_000_000.0
DAY = 24 * 3600


def sample(t, pct=None, watts=100.0, plugged=True, charging=False, ups_pct=None,
           ups_online=None):
    return HistorySample(t, pct, watts, plugged, charging, ups_pct, ups_online)


class Rows(unittest.TestCase):
    def test_round_trip(self):
        original = sample(NOW - 60, 80, 288.5, True, False, 100.0, True)
        row = original.to_row()
        self.assertEqual(row, [NOW - 60, 80, 288.5, True, False, 100.0, True])
        self.assertEqual(sample_from_row(row, NOW), original)

    def test_desktop_row_with_unknowns(self):
        row = [NOW, None, None, True, False, None, None]
        self.assertEqual(sample_from_row(row, NOW), sample(NOW, watts=None))

    def test_invalid_rows_are_rejected(self):
        good = [NOW, 50, 10.0, True, False, 90.0, True]
        replacements = {0: (math.nan, math.inf, -1, 0, True, "1", None),
                        1: (101, -1, 50.5, True, "50"),
                        2: (-0.1, math.nan, 2000.0, "1", False),
                        3: (None, 1, "true"), 4: (None, 0),
                        5: (100.1, -1.0, math.nan, True), 6: (1, "yes")}
        for index, values in replacements.items():
            for value in values:
                with self.subTest(index=index, value=value):
                    row = list(good)
                    row[index] = value
                    self.assertIsNone(sample_from_row(row, NOW))
        for row in ([], good[:6], good + [1], "row", None, {"t": NOW}):
            with self.subTest(row=row):
                self.assertIsNone(sample_from_row(row, NOW))

    def test_new_samples_beyond_the_future_tolerance_are_rejected(self):
        tolerance = History.FUTURE_TOLERANCE_SECONDS
        self.assertIsNotNone(sample_from_row(sample(NOW + tolerance).to_row(), NOW))
        self.assertIsNone(sample_from_row(sample(NOW + tolerance + 1).to_row(), NOW))

    def test_stored_rows_are_not_checked_against_the_clock(self):
        self.assertIsNotNone(sample_from_row(sample(NOW + 30 * DAY).to_row()))

    def test_sample_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            sample(NOW).watts = 1.0

    def test_imported_flag_is_not_part_of_the_row(self):
        row = HistorySample(NOW, 80, None, True, False, None, None, imported=True).to_row()
        self.assertEqual(row, [NOW, 80, None, True, False, None, None])
        self.assertFalse(sample_from_row(row, NOW).imported)

    def test_imported_flag_must_be_a_boolean(self):
        self.assertIsNone(sample_from_row([NOW, 80, None, True, False, None, None, 1], NOW))
        self.assertFalse(valid_sample(dataclasses.replace(sample(NOW), imported=1), NOW))


class Files(unittest.TestCase):
    def test_encode_and_decode(self):
        samples = (sample(NOW - 120), sample(NOW - 60, ups_pct=99.0, ups_online=True))
        data = encode_history(samples)
        self.assertEqual(json.loads(data)["format"], 1)
        self.assertEqual(json.loads(data)["samples"][1],
                         [NOW - 60, None, 100.0, True, False, 99.0, True])
        self.assertEqual(decode_history(data), (samples, 0))

    def test_invalid_rows_are_dropped_future_rows_kept_and_sorted(self):
        rows = [sample(NOW - 60).to_row(), ["bad"], sample(NOW - 120).to_row(),
                sample(NOW + 400).to_row()]
        data = json.dumps({"format": 1, "samples": rows}).encode()
        samples, dropped = decode_history(data)
        self.assertEqual([s.t for s in samples], [NOW - 120, NOW - 60, NOW + 400])
        self.assertEqual(dropped, 1)

    def test_undecodable_content(self):
        for data in (b"", b"{not json", b"\xff\xfe", b"[]", b'{"format": 1}',
                     b'{"format": 1, "samples": {}}', b'{"samples": []}',
                     b'{"format": "1", "samples": []}', b'{"format": 0, "samples": []}'):
            with self.subTest(data=data):
                with self.assertRaises(Undecodable):
                    decode_history(data)

    def test_newer_format(self):
        with self.assertRaises(NewerFormat):
            decode_history(b'{"format": 2, "samples": []}')


class Retention(unittest.TestCase):
    def test_seven_day_trim(self):
        samples = (sample(NOW - 7 * DAY - 1), sample(NOW - 7 * DAY), sample(NOW))
        self.assertEqual([s.t for s in trim(samples, NOW)], [NOW - 7 * DAY, NOW])
        self.assertEqual(trim((sample(NOW - 8 * DAY),), NOW), ())

    def test_no_trim_while_the_clock_is_behind_the_newest_sample(self):
        samples = (sample(NOW - 8 * DAY), sample(NOW + 3600))
        self.assertEqual(trim(samples, NOW), samples)


class Points(unittest.TestCase):
    def test_week_of_minutes_is_bucketed_to_240_points(self):
        samples = tuple(sample(NOW - 7 * DAY + i * 60, watts=float(i % 42))
                        for i in range(10080))
        points = chart_points(samples, 7 * DAY, NOW)
        self.assertEqual(len(points), 240)
        self.assertEqual(points[1].t - points[0].t, 42 * 60)
        self.assertEqual(points[0].watts, 20.5)
        self.assertEqual([p.id for p in points], list(range(240)))
        self.assertEqual({p.segment for p in points}, {0})
        # 區間起點相隔 42 分鐘，但資料本身每分鐘一筆：耗電線不能每點都斷開
        self.assertEqual({p.watts_segment for p in points}, {0})

    def test_bucketed_gaps_use_the_time_between_actual_samples(self):
        # 每 4 筆一個區間：第 22 筆之後空 15 分鐘，空白兩側不能被平均成同一點
        times = [NOW - 3600 + i * 60 for i in range(60) if not 22 <= i < 37]
        points = chart_points(tuple(sample(t) for t in times), 3600, NOW, max_points=15)
        self.assertLessEqual(len(points), 15)
        self.assertIn(NOW - 3600 + 37 * 60, [p.t for p in points])
        self.assertEqual(sorted({p.watts_segment for p in points}), [0, 1])
        self.assertEqual({p.segment for p in points}, {0})

    def test_long_gap_inside_a_week_breaks_both_lines(self):
        gap = range(3 * 24 * 60, 3 * 24 * 60 + 400)        # 約 6.7 小時沒有資料
        samples = tuple(sample(NOW - i * 60) for i in range(7 * 24 * 60, 0, -1) if i not in gap)
        points = chart_points(samples, 7 * DAY, NOW)
        self.assertLessEqual(len(points), History.MAX_POINTS)
        self.assertEqual(sorted({p.segment for p in points}), [0, 1])
        self.assertEqual(sorted({p.watts_segment for p in points}), [0, 1])

    def test_range_filters_older_samples(self):
        samples = (sample(NOW - 3601), sample(NOW - 3600), sample(NOW))
        self.assertEqual([p.t for p in chart_points(samples, 3600, NOW)], [NOW - 3600, NOW])
        self.assertEqual(chart_points((), 3600, NOW), ())

    def test_bucket_averages_and_plugged_any(self):
        samples = (sample(NOW - 180, pct=50, watts=10.0, plugged=False, ups_pct=90.0),
                   sample(NOW - 120, pct=None, watts=None, plugged=True, ups_pct=None),
                   sample(NOW - 60, pct=40, watts=30.0, plugged=False, ups_pct=80.0),
                   sample(NOW, pct=30, watts=50.0, plugged=False, ups_pct=70.0))
        points = chart_points(samples, 3600, NOW, max_points=3)
        self.assertEqual(len(points), 3)  # 兩個區間，接電狀態改變，中間多一個複製點
        first, last = points[0], points[-1]
        self.assertEqual((first.t, first.pct, first.watts, first.ups_pct, first.plugged),
                         (NOW - 180, 50.0, 10.0, 90.0, True))
        self.assertEqual((last.t, last.pct, last.watts, last.ups_pct, last.plugged),
                         (NOW - 60, 35.0, 40.0, 75.0, False))

    def test_gap_over_six_hours_starts_a_new_segment(self):
        gap = History.GAP_SECONDS
        samples = (sample(NOW - 2 * gap - 1), sample(NOW - gap - 1), sample(NOW))
        self.assertEqual([p.segment for p in chart_points(samples, DAY, NOW)], [0, 0, 1])

    def test_plug_change_duplicates_previous_point(self):
        samples = (sample(NOW - 120, pct=80, plugged=True, ups_pct=99.0),
                   sample(NOW - 60, pct=79, plugged=True, ups_pct=98.0),
                   sample(NOW, pct=78, plugged=False, ups_pct=97.0))
        points = chart_points(samples, 3600, NOW)
        self.assertEqual(len(points), 4)
        duplicate = points[2]
        self.assertEqual((duplicate.t, duplicate.pct, duplicate.watts, duplicate.ups_pct,
                          duplicate.plugged, duplicate.segment),
                         (NOW - 60, 79.0, None, 98.0, False, 1))
        self.assertEqual([(p.plugged, p.segment) for p in points],
                         [(True, 0), (True, 0), (False, 1), (False, 1)])
        self.assertEqual([p.id for p in points], [0, 1, 2, 3])

    def test_watts_segments_break_on_missing_values(self):
        samples = (sample(NOW - 180), sample(NOW - 120), sample(NOW - 60, watts=None),
                   sample(NOW))
        self.assertEqual([p.watts_segment for p in chart_points(samples, 3600, NOW)],
                         [0, 0, 1, 2])

    def test_watts_segments_break_after_ten_minutes(self):
        limit = History.WATTS_GAP_SECONDS
        samples = (sample(NOW - 2 * limit - 1), sample(NOW - limit - 1), sample(NOW))
        self.assertEqual([p.watts_segment for p in chart_points(samples, 3600, NOW)], [0, 0, 1])


def imported(t, pct=None, ups_pct=None, plugged=True):
    return HistorySample(t, pct, None, plugged, False, ups_pct, None, imported=True)


class ImportedPoints(unittest.TestCase):
    """由 UPower 補回的紀錄另成一段（畫成虛線），連到空白兩側自己的紀錄；自己的紀錄不受影響。"""

    def flags(self, points):
        return [(p.t, p.imported, p.segment) for p in points]

    def test_dashed_segment_bridges_the_gap_between_own_samples(self):
        own = (sample(NOW - 7200, pct=80), sample(NOW - 7140, pct=79),
               sample(NOW - 3600, pct=60), sample(NOW - 3540, pct=59))
        filled = (imported(NOW - 6000, pct=70), imported(NOW - 5000, pct=65))
        points = chart_points(own, 3 * 3600, NOW, imported=filled)
        self.assertEqual(self.flags(points), [
            (NOW - 7200, False, 0), (NOW - 7140, False, 0),
            (NOW - 7140, True, 1), (NOW - 6000, True, 1), (NOW - 5000, True, 1),
            (NOW - 3600, True, 1),
            (NOW - 3600, False, 2), (NOW - 3540, False, 2)])
        self.assertEqual([p.pct for p in points], [80, 79, 79, 70, 65, 60, 60, 59])
        self.assertEqual([p.id for p in points], list(range(8)))

    def test_without_imported_points_nothing_changes(self):
        own = tuple(sample(NOW - 3600 + i * 60, pct=50) for i in range(30))
        self.assertEqual(chart_points(own, 3600, NOW, imported=()), chart_points(own, 3600, NOW))
        self.assertFalse(any(p.imported for p in chart_points(own, 3600, NOW)))

    def test_imported_points_alone_are_drawn(self):
        filled = (imported(NOW - 3000, pct=70), imported(NOW - 2000, pct=60))
        points = chart_points((), 3600, NOW, imported=filled)
        self.assertEqual(self.flags(points), [(NOW - 3000, True, 0), (NOW - 2000, True, 0)])

    def test_buckets_never_mix_own_and_imported_values(self):
        now = int(NOW)
        own = tuple(sample(t, pct=80) for t in range(now - 7 * DAY, now - 2 * DAY, 60)) + \
            tuple(sample(t, pct=80) for t in range(now - DAY, now, 60))
        filled = tuple(imported(t, pct=30) for t in range(now - 2 * DAY + 600, now - DAY - 600,
                                                          120))
        points = chart_points(own, 7 * DAY, NOW, imported=filled)
        self.assertLessEqual(len(points), History.MAX_POINTS)
        self.assertEqual({p.pct for p in points if not p.imported}, {80})
        inner = [p for p in points if p.imported and NOW - 2 * DAY < p.t < NOW - DAY]
        self.assertTrue(inner)
        self.assertEqual({p.pct for p in inner}, {30})

    def test_merged_old_runs_use_only_own_values(self):
        """段數超過點數上限、最舊的段兩兩合併時，同一區間的補回值不會與自己的值平均。"""
        own = tuple(sample(NOW - 7 * DAY + i * 1200, pct=80) for i in range(400))
        filled = tuple(imported(NOW - 7 * DAY + i * 1200 + 300, pct=30) for i in range(400))
        points = chart_points(own, 7 * DAY, NOW, imported=filled)
        self.assertLessEqual(len(points), History.MAX_POINTS)
        self.assertLessEqual({p.pct for p in points}, {80, 30})     # 沒有平均出來的 55

    def test_imported_far_from_own_samples_is_not_bridged(self):
        own = (sample(NOW - 20 * 3600, pct=80), sample(NOW - 60, pct=40))
        filled = (imported(NOW - 10 * 3600, pct=60),)
        points = chart_points(own, DAY, NOW, imported=filled)
        self.assertEqual(self.flags(points), [(NOW - 20 * 3600, False, 0),
                                              (NOW - 10 * 3600, True, 1),
                                              (NOW - 60, False, 2)])

    def test_plug_change_inside_imported_points_stays_dashed(self):
        filled = (imported(NOW - 3000, pct=50, plugged=False),
                  imported(NOW - 2000, pct=55, plugged=True))
        points = chart_points((), 3600, NOW, imported=filled)
        self.assertEqual([(p.t, p.imported, p.plugged, p.segment) for p in points],
                         [(NOW - 3000, True, False, 0), (NOW - 3000, True, True, 1),
                          (NOW - 2000, True, True, 1)])

    def test_random_mixes_never_exceed_the_budget(self):
        rng = random.Random(20261031)
        for case in range(200):
            t, own, filled = NOW - 7 * DAY, [], []
            for _ in range(rng.randrange(0, 1500)):
                t += rng.choice((60, 60, 120, 601, 3600, 7 * 3600))
                if t > NOW:
                    break
                target = filled if rng.random() < 0.3 else own
                target.append((imported if target is filled else sample)(
                    t, pct=rng.choice((None, 50)), plugged=rng.random() < 0.5))
            limit = rng.randrange(1, 300)
            with self.subTest(case=case, limit=limit):
                points = chart_points(tuple(own), 7 * DAY, NOW, limit, imported=tuple(filled))
                self.assertLessEqual(len(points), limit)
                self.assertEqual([p.t for p in points], sorted(p.t for p in points))
                self.assertEqual([p.segment for p in points], sorted(p.segment for p in points))


class PointBudget(unittest.TestCase):
    """降採樣後的點數（含接電狀態改變的複製點）一定不超過 max_points。"""
    def assert_valid(self, points, max_points):
        self.assertLessEqual(len(points), max_points)
        self.assertEqual([p.id for p in points], list(range(len(points))))
        self.assertEqual([p.t for p in points], sorted(p.t for p in points))
        self.assertEqual([p.segment for p in points], sorted(p.segment for p in points))

    def test_plug_flip_at_every_bucket_boundary(self):
        samples = tuple(sample(NOW - 7 * DAY + i * 60, plugged=(i // 42) % 2 == 0)
                        for i in range(10080))
        self.assert_valid(chart_points(samples, 7 * DAY, NOW), History.MAX_POINTS)

    def test_four_hundred_gaps_longer_than_ten_minutes(self):
        samples = tuple(sample(NOW - 400 * 900 + i * 900) for i in range(401))
        points = chart_points(samples, 7 * DAY, NOW)
        self.assert_valid(points, History.MAX_POINTS)
        # 犧牲最舊資料的細節：最新的 79 筆仍各自一點
        self.assertEqual([p.t for p in points[-79:]], [s.t for s in samples[-79:]])

    def test_gaps_with_longer_runs(self):
        samples = tuple(sample(NOW - 6 * DAY + run * 1200 + i * 60)
                        for run in range(400) for i in range(3))
        self.assert_valid(chart_points(samples, 7 * DAY, NOW), History.MAX_POINTS)

    def test_random_data_never_exceeds_the_budget(self):
        rng = random.Random(20261021)
        for case in range(300):
            t, samples = NOW - 7 * DAY, []
            for _ in range(rng.randrange(0, 2500)):
                t += rng.choice((60, 60, 60, 120, 601, 3600, 7 * 3600))
                if t > NOW:
                    break
                samples.append(sample(t, watts=rng.choice((None, 10.0, 250.0)),
                                      plugged=rng.random() < 0.5,
                                      ups_pct=rng.choice((None, 90.0))))
            limit = rng.randrange(1, 300)
            with self.subTest(case=case, count=len(samples), limit=limit):
                self.assert_valid(chart_points(tuple(samples), 7 * DAY, NOW, limit), limit)


class LastUnplugged(unittest.TestCase):
    def at(self, minutes_ago, plugged):
        return HistorySample(NOW - minutes_ago * 60, 80, None, plugged, False, None, None)

    def setUp(self):
        # 三天前接著電，之後拔電、關機；今天開機後一直用電池
        self.samples = (self.at(3 * 24 * 60 + 10, True), self.at(3 * 24 * 60, False),
                        self.at(20, False), self.at(1, False))
        self.boot = NOW - 25 * 60

    def test_unplugged_time_starts_at_boot_after_shutdown(self):
        self.assertEqual(last_unplugged(self.samples, NOW, self.boot), self.boot)

    def test_sleep_only_keeps_original_unplug_time(self):
        early_boot = NOW - 5 * 24 * 3600
        self.assertEqual(last_unplugged(self.samples, NOW, early_boot), self.samples[1].t)

    def test_still_plugged_or_too_old_is_none(self):
        self.assertIsNone(last_unplugged(self.samples + (self.at(0.5, True),), NOW, self.boot))
        self.assertIsNone(last_unplugged(self.samples[:3], NOW, self.boot))

    def test_never_plugged_or_empty_is_none(self):
        self.assertIsNone(last_unplugged(self.samples[1:], NOW, self.boot))
        self.assertIsNone(last_unplugged((), NOW, self.boot))

    def test_clamp_without_boot_time(self):
        self.assertEqual(clamp_to_boot(self.samples[1].t, None), self.samples[1].t)


class BootTime(unittest.TestCase):
    def test_parse_btime(self):
        self.assertEqual(parse_boot_time("cpu  1 2 3\nbtime 1791279265\nprocesses 9\n"),
                         1791279265.0)
        self.assertIsNone(parse_boot_time("cpu 1 2 3\n"))
        self.assertIsNone(parse_boot_time("btime soon\n"))


if __name__ == "__main__":
    unittest.main()
