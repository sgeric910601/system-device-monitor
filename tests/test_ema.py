"""近 3 分鐘平均（指數移動平均，τ = 180 秒），與參考專案 BatteryStore.ema 相同。"""
import unittest

from sdm.constants import Power
from sdm.core.ema import Ema, ema


class Function(unittest.TestCase):
    def test_one_time_constant_moves_63_percent(self):
        self.assertEqual(Power.EMA_TAU_SECONDS, 180)
        self.assertEqual(round(ema(100.0, 200.0, 180.0), 2), 163.21)

    def test_first_sample_is_the_value(self):
        self.assertEqual(ema(None, 200.0, 180.0), 200.0)

    def test_non_positive_interval_takes_the_new_value(self):
        self.assertEqual(ema(100.0, 200.0, 0.0), 200.0)
        self.assertEqual(ema(100.0, 200.0, -5.0), 200.0)

    def test_custom_time_constant(self):
        self.assertEqual(round(ema(0.0, 100.0, 60.0, tau=60.0), 2), 63.21)


class Average(unittest.TestCase):
    def test_updates_with_elapsed_time(self):
        average = Ema()
        self.assertEqual(average.update(100.0, now=0.0), 100.0)
        self.assertEqual(round(average.update(200.0, now=180.0), 2), 163.21)

    def test_missing_value_keeps_average_but_advances_time(self):
        average = Ema()
        average.update(100.0, now=0.0)
        self.assertEqual(average.update(None, now=170.0), 100.0)
        self.assertEqual(round(average.update(200.0, now=350.0), 2), 163.21)

    def test_reset_starts_over(self):
        average = Ema()
        average.update(100.0, now=0.0)
        average.reset()
        self.assertIsNone(average.value)
        self.assertEqual(average.update(50.0, now=200.0), 50.0)

    def test_empty_average_with_missing_value(self):
        self.assertIsNone(Ema().update(None, now=1.0))


if __name__ == "__main__":
    unittest.main()
