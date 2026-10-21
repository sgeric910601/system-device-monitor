"""歷史紀錄純邏輯：列驗證、7 天保留、圖表降採樣與分段、拔電時間。

參考專案 HistoryTests.testUnpluggedTimeStartsAtBootAfterShutdown 全部案例以相同數字移植
（LastUnplugged 類別）。參考專案的 pmset 回填只適用 macOS，Linux 沒有對應來源，不移植。
"""
import dataclasses
import json
import math
import unittest

from sdm.constants import History
from sdm.core.history import (HistorySample, NewerFormat, Undecodable, chart_points,
                              clamp_to_boot, decode_history, encode_history, last_unplugged,
                              parse_boot_time, sample_from_row, trim)

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

    def test_future_rows_beyond_tolerance_are_rejected(self):
        tolerance = History.FUTURE_TOLERANCE_SECONDS
        self.assertIsNotNone(sample_from_row(sample(NOW + tolerance).to_row(), NOW))
        self.assertIsNone(sample_from_row(sample(NOW + tolerance + 1).to_row(), NOW))

    def test_sample_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            sample(NOW).watts = 1.0


class Files(unittest.TestCase):
    def test_encode_and_decode(self):
        samples = (sample(NOW - 120), sample(NOW - 60, ups_pct=99.0, ups_online=True))
        data = encode_history(samples)
        self.assertEqual(json.loads(data)["format"], 1)
        self.assertEqual(json.loads(data)["samples"][1],
                         [NOW - 60, None, 100.0, True, False, 99.0, True])
        self.assertEqual(decode_history(data, NOW), (samples, 0))

    def test_invalid_rows_are_dropped_and_rest_sorted(self):
        rows = [sample(NOW - 60).to_row(), ["bad"], sample(NOW - 120).to_row(),
                sample(NOW + 400).to_row()]
        data = json.dumps({"format": 1, "samples": rows}).encode()
        samples, dropped = decode_history(data, NOW)
        self.assertEqual([s.t for s in samples], [NOW - 120, NOW - 60])
        self.assertEqual(dropped, 2)

    def test_undecodable_content(self):
        for data in (b"", b"{not json", b"\xff\xfe", b"[]", b'{"format": 1}',
                     b'{"format": 1, "samples": {}}', b'{"samples": []}',
                     b'{"format": "1", "samples": []}', b'{"format": 0, "samples": []}'):
            with self.subTest(data=data):
                with self.assertRaises(Undecodable):
                    decode_history(data, NOW)

    def test_newer_format(self):
        with self.assertRaises(NewerFormat):
            decode_history(b'{"format": 2, "samples": []}', NOW)


class Retention(unittest.TestCase):
    def test_seven_day_trim(self):
        samples = (sample(NOW - 7 * DAY - 1), sample(NOW - 7 * DAY), sample(NOW))
        self.assertEqual([s.t for s in trim(samples, NOW)], [NOW - 7 * DAY, NOW])
        self.assertEqual(trim((sample(NOW - 8 * DAY),), NOW), ())


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

    def test_range_filters_older_samples(self):
        samples = (sample(NOW - 3601), sample(NOW - 3600), sample(NOW))
        self.assertEqual([p.t for p in chart_points(samples, 3600, NOW)], [NOW - 3600, NOW])
        self.assertEqual(chart_points((), 3600, NOW), ())

    def test_bucket_averages_and_plugged_any(self):
        samples = (sample(NOW - 180, pct=50, watts=10.0, plugged=False, ups_pct=90.0),
                   sample(NOW - 120, pct=None, watts=None, plugged=True, ups_pct=None),
                   sample(NOW - 60, pct=40, watts=30.0, plugged=False, ups_pct=80.0),
                   sample(NOW, pct=30, watts=50.0, plugged=False, ups_pct=70.0))
        points = chart_points(samples, 3600, NOW, max_points=2)
        self.assertEqual(len(points), 3)  # 接電狀態改變，中間多一個複製點
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
