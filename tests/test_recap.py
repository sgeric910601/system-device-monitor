"""今日電量回顧：移植參考專案 ChargingPlanTests.testDailyRecap 的數值。"""
from datetime import datetime, timezone
import unittest

from sdm.core.history import HistorySample
from sdm.core.recap import DailyRecap, day_bounds, make_recap

UTC = timezone.utc
MIDNIGHT = datetime(2026, 10, 13, tzinfo=UTC).timestamp()


def sample(minutes, pct, plugged):
    return HistorySample(t=MIDNIGHT + minutes * 60, pct=pct, watts=None, plugged=plugged,
                         charging=plugged, ups_pct=None, ups_online=None)


def reference_day():
    samples = [sample(-10, 60, True)]
    # 0:00–8:00 接電 60→80（中間在 80 附近 ±1 跳動不重複算）
    samples += [sample(m, min(80, 60 + m // 20), True) for m in range(0, 481, 10)]
    samples += [sample(490, 79, True), sample(500, 80, True)]
    # 8:30 拔電，用到 12:00 剩 50%
    samples += [sample(m, 80 - (m - 510) // 7, False) for m in range(510, 721, 10)]
    # 12:10 接電 30 分鐘到 60%
    samples += [sample(730, 50, True), sample(760, 60, True)]
    # 13:00 再拔電用到 14:00
    samples += [sample(m, 60 - (m - 780) // 10, False) for m in range(780, 841, 10)]
    return samples


class Recap(unittest.TestCase):
    def test_reference_numbers(self):
        recap = make_recap(reference_day(), MIDNIGHT + 15 * 3600, UTC)
        self.assertEqual((recap.start_percent, recap.end_percent), (60, 54))
        self.assertEqual(recap.plug_in_count, 1)
        self.assertEqual((recap.charged_percent, recap.used_percent, recap.net_change), (30, 36, -6))
        # 8:30–12:10 共 220 分鐘、13:00–14:00 共 60 分鐘
        self.assertEqual((recap.unplugged_minutes, recap.longest_unplugged_minutes), (280, 220))

    def test_order_does_not_matter(self):
        shuffled = list(reversed(reference_day()))
        self.assertEqual(make_recap(shuffled, MIDNIGHT + 15 * 3600, UTC),
                         make_recap(reference_day(), MIDNIGHT + 15 * 3600, UTC))

    def test_no_samples_or_desktop_rows(self):
        self.assertIsNone(make_recap([], MIDNIGHT, UTC).start_percent)
        desktop = HistorySample(t=MIDNIGHT, pct=None, watts=30.0, plugged=True, charging=False,
                                ups_pct=100.0, ups_online=True)
        recap = make_recap([desktop], MIDNIGHT, UTC)
        self.assertEqual(recap, DailyRecap())
        self.assertIsNone(recap.net_change)

    def test_yesterday_only_is_empty(self):
        self.assertEqual(make_recap([sample(-10, 60, True)], MIDNIGHT + 3600, UTC), DailyRecap())

    def test_long_gaps_count_at_most_thirty_minutes(self):
        samples = [sample(0, 90, False), sample(240, 70, False)]
        recap = make_recap(samples, MIDNIGHT, UTC)
        self.assertEqual((recap.unplugged_minutes, recap.used_percent), (30, 20))

    def test_day_bounds(self):
        self.assertEqual(day_bounds(MIDNIGHT + 5 * 3600, UTC), (MIDNIGHT, MIDNIGHT + 24 * 3600))


if __name__ == "__main__":
    unittest.main()
