"""圖表與電力流向圖的幾何計算：不需要顯示器即可測試。"""
from datetime import datetime, timezone
import math
import unittest

from sdm.core.chart_layout import (axis_ticks, band_widths, flow_geometry, line_segments,
                                   linear, nice_axis_max, pct_segments, ups_segments,
                                   watts_segments,
                                   watts_summary)
from sdm.core.history import ChartPoint

END = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc).timestamp()


def point(index, watts, t=0.0):
    return ChartPoint(index, t, None, watts, None, True, 0, 0)


class Axis(unittest.TestCase):
    def test_nice_axis_max_rounds_up_to_one_two_five(self):
        cases = ((288, 500), (61.5, 100), (199, 200), (200, 200), (1000, 1000), (1001, 2000),
                 (5, 10), (0, 10), (math.nan, 10), (-3, 10))
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(nice_axis_max(value, minimum=10), expected)

    def test_reference_default_minimum(self):
        self.assertEqual(nice_axis_max(0), 1000)
        self.assertEqual(nice_axis_max(4500), 5000)

    def test_linear_mapping(self):
        self.assertEqual(linear(250, 0, 500, 200, 0), 100)
        self.assertEqual(linear(5, 5, 5, 0, 10), 0)


class TimeTicks(unittest.TestCase):
    def labels(self, range_s):
        return [label for _, label in axis_ticks(END, range_s, timezone.utc)]

    def test_hour_range_every_quarter_hour_away_from_edges(self):
        self.assertEqual(self.labels(3600), ["11:15", "11:30", "11:45"])

    def test_day_range_every_six_hours(self):
        self.assertEqual(self.labels(86400), ["18:00", "00:00", "06:00"])

    def test_week_range_by_day(self):
        self.assertEqual(self.labels(7 * 86400),
                         ["10/15", "10/16", "10/17", "10/18", "10/19", "10/20", "10/21"])

    def test_ticks_are_inside_the_range(self):
        for range_s in (3600, 86400, 7 * 86400):
            for t, _ in axis_ticks(END, range_s, timezone.utc):
                self.assertTrue(END - range_s < t < END)


class Bands(unittest.TestCase):
    def test_square_root_scaling(self):
        widths = band_widths((61.5, 226.5), 288.0, 40.0)
        self.assertAlmostEqual(widths[0], 40 * math.sqrt(61.5 / 288), places=6)
        self.assertAlmostEqual(widths[1], 40 * math.sqrt(226.5 / 288), places=6)
        self.assertEqual(band_widths((288.0,), 288.0, 40.0), (40.0,))

    def test_minimum_width_and_missing_values(self):
        self.assertEqual(band_widths((0.01, None, 0.0, -1.0), 288.0, 40.0), (2.0, 0.0, 0.0, 0.0))

    def test_values_above_reference_are_capped(self):
        self.assertEqual(band_widths((400.0,), 288.0, 40.0), (40.0,))

    def test_missing_reference_uses_the_largest_value(self):
        self.assertEqual(band_widths((25.0, 100.0), None, 40.0), (20.0, 40.0))
        self.assertEqual(band_widths((None,), None, 40.0), (0.0,))


class Flow(unittest.TestCase):
    def test_layout_for_two_items(self):
        geometry = flow_geometry(900, 220, (61.5, 226.5), 288.0)
        self.assertEqual(len(geometry.items), 2)
        self.assertEqual(len(geometry.item_bands), 2)
        source, total = geometry.source, geometry.total
        self.assertLess(source.x + source.width, total.x)
        for rect in (source, total) + geometry.items:
            self.assertGreaterEqual(rect.x, 0)
            self.assertLessEqual(rect.x + rect.width, 900)
            self.assertGreaterEqual(rect.y, 0)
            self.assertLessEqual(rect.y + rect.height, 220)
        first, second = geometry.items
        self.assertLessEqual(first.y + first.height, second.y)
        widths = [band.width for band in geometry.item_bands]
        self.assertLess(widths[0], widths[1])
        self.assertEqual(geometry.source_band.width, max(widths[1], geometry.source_band.width))

    def test_bands_fit_on_the_total_node(self):
        geometry = flow_geometry(600, 160, (100.0,) * 6, 600.0)
        stacked = sum(band.width for band in geometry.item_bands)
        self.assertLessEqual(stacked, geometry.total.height)
        tops = [band.y0 - band.width / 2 for band in geometry.item_bands]
        self.assertGreaterEqual(min(tops), geometry.total.y)

    def test_band_endpoints_meet_the_nodes(self):
        geometry = flow_geometry(900, 220, (61.5,), 288.0)
        band, item = geometry.item_bands[0], geometry.items[0]
        self.assertEqual(band.x0, geometry.total.x + geometry.total.width)
        self.assertEqual(band.x1, item.x)
        self.assertEqual(band.y1, item.y + item.height / 2)
        self.assertEqual(geometry.source_band.x0, geometry.source.x + geometry.source.width)

    def test_narrow_width_shrinks_the_nodes_instead_of_overflowing(self):
        from sdm.constants import Flow as FlowSizes
        for width in (FlowSizes.MIN_WIDTH, 380, 330):
            geometry = flow_geometry(width, 160, (61.5, 226.5), 288.0)
            source, total = geometry.source, geometry.total
            with self.subTest(width=width):
                self.assertLess(source.x + source.width, total.x)
                self.assertLess(total.x + total.width, geometry.items[0].x)
                for rect in (source, total) + geometry.items:
                    self.assertLessEqual(rect.x + rect.width, width)
        wide = flow_geometry(900, 160, (61.5,), 288.0)
        self.assertEqual(wide.items[0].width, FlowSizes.ITEM_WIDTH)

    def test_no_items(self):
        geometry = flow_geometry(900, 220, (), 288.0)
        self.assertEqual((geometry.items, geometry.item_bands), ((), ()))


def chart_point(index, t, watts=None, ups=None, segment=0, watts_segment=0, plugged=True):
    return ChartPoint(index, t, None, watts, ups, plugged, segment, watts_segment)


class Segments(unittest.TestCase):
    def test_watts_runs_follow_watts_segment_and_skip_the_duplicate_point(self):
        points = (chart_point(0, 0, 10.0), chart_point(1, 60, 20.0),
                  chart_point(2, 60, None, segment=1),          # 接電狀態改變的複製點
                  chart_point(3, 120, 30.0, segment=1),
                  chart_point(4, 900, 40.0, segment=1, watts_segment=1))
        self.assertEqual(watts_segments(points),
                         [[(0, 10.0, True), (60, 20.0, True), (120, 30.0, True)],
                          [(900, 40.0, True)]])

    def test_ups_runs_break_on_missing_values_and_segments(self):
        points = (chart_point(0, 0, ups=100.0), chart_point(1, 60, ups=None),
                  chart_point(2, 120, ups=99.0), chart_point(3, 180, ups=98.0),
                  chart_point(4, 180, ups=98.0, segment=1, plugged=False),
                  chart_point(5, 240, ups=90.0, segment=1, plugged=False))
        self.assertEqual(ups_segments(points),
                         [[(0, 100.0, True)], [(120, 99.0, True), (180, 98.0, True)],
                          [(180, 98.0, False), (240, 90.0, False)]])

    def test_battery_runs_are_coloured_by_plug_state(self):
        points = (ChartPoint(0, 0, 80.0, 6.0, None, False, 0, 0),
                  ChartPoint(1, 60, 79.0, None, None, False, 0, 1),
                  ChartPoint(2, 60, 79.0, None, None, True, 1, 1),      # 接上電源的複製點
                  ChartPoint(3, 120, 81.0, 20.0, None, True, 1, 1),
                  ChartPoint(4, 180, None, 5.0, None, True, 1, 1))
        self.assertEqual(pct_segments(points),
                         [[(0, 80.0, False), (60, 79.0, False)],
                          [(60, 79.0, True), (120, 81.0, True)]])

    def test_generic_split(self):
        self.assertEqual(line_segments((), lambda p: p.watts, lambda p: 0), [])


class Summary(unittest.TestCase):
    def test_average_and_peak(self):
        points = (point(0, 280.0), point(1, None), point(2, 300.5))
        self.assertEqual(watts_summary(points), "平均耗電 290.2 W · 最高 300.5 W")

    def test_no_watts(self):
        self.assertIsNone(watts_summary((point(0, None),)))
        self.assertIsNone(watts_summary(()))


if __name__ == "__main__":
    unittest.main()
