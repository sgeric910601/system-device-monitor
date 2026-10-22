"""系統監控頁的數字格式（參考專案 ByteFormat 與 MonitorTests 的數值照搬）。"""
import math
import unittest

from sdm.core import byte_format as fmt


class ReferenceCases(unittest.TestCase):
    def test_byte_formats(self):
        # MonitorTests.testByteFormats
        self.assertEqual(fmt.size(494_384_795_648), "494 GB")
        self.assertEqual(fmt.memory(5_952_618_496), "5.5 GB")
        self.assertEqual(fmt.rate(1_234_000), "1.2 MB/s")
        self.assertEqual(fmt.compact_rate(0), "0K")
        self.assertEqual(fmt.compact_rate(340_000), "340K")
        self.assertEqual(fmt.compact_rate(1_234_000), "1.2M")
        self.assertEqual(fmt.compact_rate(25_000_000), "25M")
        self.assertEqual(fmt.percent(0.195), "20%")


class Details(unittest.TestCase):
    def test_small_values_and_bytes(self):
        self.assertEqual(fmt.size(0), "0 B")
        self.assertEqual(fmt.size(999), "999 B")
        self.assertEqual(fmt.size(2_000_398_934_016), "2.0 TB")
        self.assertEqual(fmt.memory(1024), "1.0 KB")
        self.assertEqual(fmt.rate(0), "0 B/s")

    def test_rounding_carries_into_the_next_unit(self):
        # 四捨五入後達到 1000 就改用下一個單位；小於 10 但進位成 10 時不留小數
        self.assertEqual(fmt.size(999_960), "1.0 MB")
        self.assertEqual(fmt.size(999_499), "999 KB")
        self.assertEqual(fmt.size(9_960), "10 KB")
        self.assertEqual(fmt.size(9_940), "9.9 KB")
        self.assertEqual(fmt.size(999_999_999_999_999), "1000 TB")     # 已是最大單位
        self.assertEqual(fmt.rate(999_960), "1.0 MB/s")
        self.assertEqual(fmt.rate(9_960), "10 KB/s")
        self.assertEqual(fmt.rate(999.6), "1.0 KB/s")
        self.assertEqual(fmt.memory(1023 * 1024 ** 2), "1.0 GB")
        self.assertEqual(fmt.memory(int(9.97 * 1024 ** 2)), "10 MB")
        self.assertEqual(fmt.memory(512 * 1024 ** 2), "512 MB")

    def test_negative_and_invalid_values(self):
        self.assertEqual(fmt.size(-5), "0 B")
        self.assertEqual(fmt.compact_rate(-1), "0K")
        for value in (None, math.nan, math.inf):
            with self.subTest(value=value):
                self.assertEqual(fmt.size(value), fmt.PLACEHOLDER)
                self.assertEqual(fmt.rate(value), fmt.PLACEHOLDER)
                self.assertEqual(fmt.compact_rate(value), fmt.PLACEHOLDER)
                self.assertEqual(fmt.percent(value), fmt.PLACEHOLDER)

    def test_compact_rate_switches_unit_before_showing_1000(self):
        self.assertEqual(fmt.compact_rate(999_400), "999K")
        self.assertEqual(fmt.compact_rate(999_600), "1.0M")
        self.assertEqual(fmt.compact_rate(5_000_000_000_000), "5000G")

    def test_axis_rate(self):
        # 參考 ByteFormat.axisRate：低於 1 KB 寫位元組，個位數保留一位小數
        self.assertEqual(fmt.axis_rate(0), "0")
        self.assertEqual(fmt.axis_rate(500), "500B")
        self.assertEqual(fmt.axis_rate(2_500_000), "2.5M")
        self.assertEqual(fmt.axis_rate(2_000_000), "2M")
        self.assertEqual(fmt.axis_rate(50_000), "50K")

    def test_percent_rounds_half_up(self):
        self.assertEqual(fmt.percent(0.125), "13%")
        self.assertEqual(fmt.percent(0), "0%")
        self.assertEqual(fmt.percent(1), "100%")


if __name__ == "__main__":
    unittest.main()
