"""記憶體組成、Swap／zswap 與記憶體壓力（以合成的 /proc/meminfo 計算）。

不照搬：MonitorTests.testMemoryCategoriesMatchActivityMonitor（macOS vm_statistics 的頁面分類，
Linux 沒有對應欄位，改用 /proc/meminfo 的 AnonPages、Shmem、Slab 等）。
"""
import unittest

from sdm.core import memory
from sdm.core.memory import NORMAL, WARNING, CRITICAL, Pressure
from tests.fixtures import procfs

GIB = 1024 ** 3
MIB = 1024 ** 2


def meminfo_text(**overrides):
    values = {**procfs.MEMINFO, **overrides}
    return "".join(f"{key}: {value} kB\n" for key, value in values.items() if value is not None)


class Composition(unittest.TestCase):
    def setUp(self):
        self.result = memory.composition(memory.parse_meminfo(meminfo_text()))

    def test_categories(self):
        result = self.result
        self.assertEqual(result.total, 16 * GIB)
        self.assertEqual(result.app, 7 * GIB)
        self.assertEqual(result.shared, 512 * MIB)
        self.assertEqual(result.kernel, (256 + 32 + 96) * MIB)
        self.assertEqual(result.cache, (4096 + 256 + 512 - 512) * MIB)
        self.assertEqual(result.free, 2 * GIB)
        self.assertEqual(result.available, 6 * GIB)

    def test_segments_fill_the_total(self):
        segments = self.result.segments()
        self.assertEqual([key for key, _ in segments],
                         ["app", "shared", "kernel", "cache", "other", "free"])
        self.assertEqual(sum(value for _, value in segments), 16 * GIB)
        self.assertEqual(dict(segments)["other"], int(1.875 * GIB))

    def test_used_is_total_minus_available(self):
        # 與總覽頁、頂端列相同的定義，數字才會一致
        self.assertEqual(self.result.used, 10 * GIB)
        self.assertAlmostEqual(self.result.used_fraction, 0.625)

    def test_swap_and_zswap(self):
        self.assertEqual((self.result.swap_used, self.result.swap_total), (GIB, 8 * GIB))
        self.assertEqual((self.result.zswap, self.result.zswapped), (128 * MIB, 384 * MIB))

    def test_cache_and_other_never_go_negative(self):
        result = memory.composition(memory.parse_meminfo(meminfo_text(
            Shmem=6 * 1024 * 1024, AnonPages=20 * 1024 * 1024)))
        self.assertEqual(result.cache, 0)
        self.assertEqual(dict(result.segments())["other"], 0)

    def test_missing_fields(self):
        result = memory.composition(memory.parse_meminfo(meminfo_text(
            MemAvailable=None, Zswap=None, Zswapped=None, SwapTotal=None, SwapFree=None)))
        self.assertEqual(result.used, 14 * GIB)            # 沒有 MemAvailable 時改用 MemFree
        self.assertIsNone(result.zswap)
        self.assertEqual((result.swap_used, result.swap_total), (0, 0))
        self.assertIsNone(memory.composition({}))
        self.assertIsNone(memory.composition(memory.parse_meminfo("garbage\n")))

    def test_parse_handles_units(self):
        parsed = memory.parse_meminfo("MemTotal: 2 kB\nHugePages_Total: 3\nBroken line\nX: y kB\n")
        self.assertEqual(parsed, {"MemTotal": 2048, "HugePages_Total": 3})


class PressureLevels(unittest.TestCase):
    def setUp(self):
        self.healthy = memory.composition(memory.parse_meminfo(meminfo_text()))

    def available(self, fraction):
        kilobytes = int(procfs.MEMINFO["MemTotal"] * fraction)
        return memory.composition(memory.parse_meminfo(meminfo_text(MemAvailable=kilobytes)))

    def test_parse_pressure(self):
        text = ("some avg10=12.50 avg60=1.00 avg300=0.00 total=5517457\n"
                "full avg10=0.40 avg60=0.00 avg300=0.00 total=5469692\n")
        self.assertEqual(memory.parse_pressure(text), Pressure(12.5, 0.4))
        self.assertIsNone(memory.parse_pressure(""))
        self.assertIsNone(memory.parse_pressure("some avg10=x\nfull avg10=1\n"))
        self.assertEqual(memory.parse_pressure("some avg10=1.00 total=3\n"), Pressure(1.0, None))

    def test_levels(self):
        self.assertEqual(memory.pressure_level(self.healthy, Pressure(0.0, 0.0)), NORMAL)
        self.assertEqual(memory.pressure_level(self.healthy, Pressure(10.0, 0.0)), WARNING)
        self.assertEqual(memory.pressure_level(self.healthy, Pressure(9.99, 4.99)), NORMAL)
        self.assertEqual(memory.pressure_level(self.healthy, Pressure(50.0, 5.0)), CRITICAL)
        self.assertEqual(memory.pressure_level(self.available(0.09), None), WARNING)
        self.assertEqual(memory.pressure_level(self.available(0.04), Pressure(0.0, 0.0)),
                         CRITICAL)
        self.assertEqual(memory.pressure_level(self.available(0.125), None), NORMAL)
        self.assertEqual(memory.pressure_level(None, None), NORMAL)


if __name__ == "__main__":
    unittest.main()
