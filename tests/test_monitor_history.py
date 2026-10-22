"""系統監控頁的 2 分鐘曲線緩衝（參考專案 MonitorHistory 的測試數值照搬）。"""
import unittest

from sdm.constants import Monitor
from sdm.core import monitor_history
from sdm.core.monitor_history import MonitorHistory

START = 1_000_000.0


class Capacity(unittest.TestCase):
    def test_history_keeps_last_points(self):
        # MonitorTests.testHistoryKeepsLastPoints：同一時間點加入 capacity + 30 筆
        history = MonitorHistory()
        for index in range(Monitor.HISTORY_CAPACITY + 30):
            history = history.appended(float(index), t=START)
        self.assertEqual(len(history.points), Monitor.HISTORY_CAPACITY)
        self.assertEqual(history.points[0].a, 30)

    def test_history_keeps_two_minutes_at_a_two_second_interval(self):
        # MonitorTests.testHistoryKeepsTwoMinutesInPowerSaving
        history = MonitorHistory()
        for index in range(100):
            history = history.appended(float(index), t=START + index * 2)
        self.assertEqual(len(history.points), 61)
        self.assertEqual(history.points[0].a, 39)
        every_second = MonitorHistory()
        for index in range(200):
            every_second = every_second.appended(float(index), t=START + index)
        self.assertEqual(len(every_second.points), Monitor.HISTORY_CAPACITY)


class Values(unittest.TestCase):
    def test_constants_match_the_reference(self):
        self.assertEqual((Monitor.HISTORY_CAPACITY, Monitor.HISTORY_WINDOW_SECONDS), (120, 120))

    def test_second_line_and_immutability(self):
        empty = MonitorHistory()
        one = empty.appended(1.0, 2.0, t=START)
        self.assertEqual(empty.points, ())
        self.assertEqual((one.points[0].a, one.points[0].b, one.points[0].t), (1.0, 2.0, START))
        self.assertEqual(one.latest(), one.points[0])
        self.assertIsNone(empty.latest())

    def test_max_value_over_both_lines(self):
        history = MonitorHistory().appended(3.0, 9.0, t=START).appended(5.0, 1.0, t=START + 1)
        self.assertEqual(history.max_value(), 9.0)
        self.assertEqual(MonitorHistory().max_value(), 0.0)

    def test_none_values_are_stored_as_gaps(self):
        history = MonitorHistory().appended(None, t=START).appended(4.0, None, t=START + 2)
        self.assertIsNone(history.points[0].a)
        self.assertIsNone(history.points[1].b)
        self.assertEqual(history.max_value(), 4.0)


class Drawing(unittest.TestCase):
    def history(self, values, times):
        history = MonitorHistory()
        for value, stamp in zip(values, times):
            history = history.appended(value, value, t=START + stamp)
        return history

    def test_runs_break_at_missing_values_and_gaps(self):
        history = self.history([1.0, None, 3.0, 4.0, 5.0, 6.0], [0, 2, 4, 6, 20, 22])
        runs = monitor_history.runs(history, "a")
        self.assertEqual([[value for _, value in run] for run in runs], [[1.0], [3.0, 4.0],
                                                                         [5.0, 6.0]])
        self.assertEqual(runs[1][0][0], START + 4)
        self.assertEqual(len(monitor_history.runs(history, "b")), 3)
        self.assertEqual(monitor_history.runs(MonitorHistory(), "a"), ())

    def test_axis_maximum(self):
        history = self.history([1500.0, 200.0], [0, 2])
        self.assertEqual(monitor_history.axis_max(history, percent=True), 100.0)
        self.assertEqual(monitor_history.axis_max(history, percent=False), 2000.0)
        self.assertEqual(monitor_history.axis_max(MonitorHistory(), percent=False), 1000.0)


if __name__ == "__main__":
    unittest.main()
