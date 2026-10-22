"""CPU 使用者／系統比例、各核心、平均負載與時脈（參考專案 MonitorTests 的 CPU 數值照搬）。

不照搬：testTopParsing（macOS 的 top 輸出格式；Linux 改由程序群組計算各 App 用量）。
"""
import unittest

from sdm.core import cpu
from sdm.core.cpu import CpuTicks
from tests.fixtures import procfs


class ReferenceCases(unittest.TestCase):
    def test_usage_from_tick_deltas(self):
        # MonitorTests.testCPUUsageFromTickDeltas
        old = CpuTicks(user=100, system=50, idle=850, nice=0)
        new = CpuTicks(user=160, system=70, idle=1070, nice=0)
        usage = cpu.usage(old, new)
        self.assertLess(abs(usage.user - 0.2), 1e-9)
        self.assertLess(abs(usage.system - 0.0666667), 1e-6)
        self.assertLess(abs(usage.total - 0.2666667), 1e-6)

    def test_counter_going_backwards_returns_none(self):
        # MonitorTests.testCPUCounterWrapReturnsNil
        old = CpuTicks(user=4_000_000_000, system=10, idle=10, nice=0)
        new = CpuTicks(user=5, system=20, idle=20, nice=0)
        self.assertIsNone(cpu.usage(old, new))

    def test_per_core_and_total(self):
        # MonitorTests.testPerCoreAndTotal
        zero = CpuTicks(user=0, system=0, idle=0, nice=0)
        old = [zero, zero]
        new = [CpuTicks(user=100, system=0, idle=0, nice=0),
               CpuTicks(user=0, system=0, idle=100, nice=0)]
        total, cores = cpu.usages(old, new)
        self.assertEqual([core.total for core in cores], [1, 0])
        self.assertEqual(total.total, 0.5)


class Usage(unittest.TestCase):
    def test_nice_counts_as_user_time(self):
        usage = cpu.usage(CpuTicks(0, 0, 0, 0), CpuTicks(user=10, system=0, idle=20, nice=10))
        self.assertAlmostEqual(usage.user, 0.5)

    def test_no_elapsed_ticks(self):
        ticks = CpuTicks(1, 1, 1, 1)
        self.assertIsNone(cpu.usage(ticks, ticks))

    def test_core_count_change_gives_no_result(self):
        zero = CpuTicks(0, 0, 0, 0)
        self.assertIsNone(cpu.usages([zero], [zero, zero]))
        self.assertIsNone(cpu.usages([], []))

    def test_core_that_went_backwards_reads_zero(self):
        old = [CpuTicks(0, 0, 0, 0), CpuTicks(500, 0, 0, 0)]
        new = [CpuTicks(50, 0, 50, 0), CpuTicks(10, 0, 100, 0)]
        self.assertIsNone(cpu.usages(old, new))   # 總計也倒退：整組不採用
        old = [CpuTicks(0, 0, 0, 0), CpuTicks(5, 0, 0, 0)]
        new = [CpuTicks(50, 0, 50, 0), CpuTicks(4, 0, 100, 0)]
        total, cores = cpu.usages(old, new)
        self.assertEqual((cores[0].total, cores[1].total), (0.5, 0))


class ProcStat(unittest.TestCase):
    def test_fields_are_grouped_like_the_reference(self):
        # user nice system idle iowait irq softirq steal guest guest_nice
        total, cores = cpu.parse_stat("cpu  10 2 3 40 5 6 7 8 9 1\ncpu0 10 2 3 40 5 6 7 8 9 1\n")
        # guest 已含在 user 裡，不再加；系統＝system＋irq＋softirq＋steal；閒置＝idle＋iowait
        self.assertEqual(total, CpuTicks(user=10, system=24, idle=45, nice=2))
        self.assertEqual(cores, (total,))

    def test_fixture_samples(self):
        first = cpu.parse_stat(procfs.STAT[0])
        second = cpu.parse_stat(procfs.STAT[1])
        total, cores = cpu.usages(first[1], second[1])
        self.assertAlmostEqual(total.total, 0.25)
        self.assertEqual([round(core.total, 3) for core in cores], [0.5, 0])
        self.assertAlmostEqual(cpu.usage(first[0], second[0]).system, 0.05)

    def test_malformed_text(self):
        self.assertIsNone(cpu.parse_stat(""))
        self.assertIsNone(cpu.parse_stat("cpu  a b c d\n"))
        self.assertIsNone(cpu.parse_stat("cpu  1 2 3\n"))


class Tracker(unittest.TestCase):
    def test_first_sample_is_a_baseline_and_a_long_gap_resets(self):
        tracker = cpu.CpuTracker()
        self.assertIsNone(tracker.sample(procfs.STAT[0], 100.0))
        result = tracker.sample(procfs.STAT[1], 102.0)
        self.assertAlmostEqual(result.total.total, 0.25)
        self.assertEqual(len(result.cores), 2)
        self.assertIsNone(tracker.sample(procfs.STAT[0], 200.0))   # 睡眠或頁面隱藏太久

    def test_total_only_without_per_core_lines(self):
        tracker = cpu.CpuTracker()
        tracker.sample("cpu  1 0 1 8\n", 1.0)
        result = tracker.sample("cpu  3 0 2 15\n", 3.0)
        self.assertAlmostEqual(result.total.total, 0.3)
        self.assertEqual(result.cores, ())

    def test_unreadable_stat(self):
        tracker = cpu.CpuTracker()
        self.assertIsNone(tracker.sample("", 1.0))
        self.assertIsNone(tracker.sample(procfs.STAT[0], 2.0))


class LoadAndFrequency(unittest.TestCase):
    def test_load_average(self):
        self.assertEqual(cpu.parse_loadavg("0.75 1.53 1.86 1/7575 7776\n"), (0.75, 1.53, 1.86))
        for text in ("", "x y z", "1.0 2.0", "nan 1 2 1/2 3", "-1 1 1 1/2 3"):
            with self.subTest(text=text):
                self.assertIsNone(cpu.parse_loadavg(text))

    def test_average_frequency_in_megahertz(self):
        self.assertEqual(cpu.average_mhz(["4200000", "3800000\n"]), 4000.0)
        self.assertEqual(cpu.average_mhz(["4200000", "", "bad", "0"]), 4200.0)
        self.assertIsNone(cpu.average_mhz([]))
        self.assertIsNone(cpu.average_mhz(["", "0"]))


if __name__ == "__main__":
    unittest.main()
