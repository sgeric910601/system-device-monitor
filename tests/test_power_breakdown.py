"""Linux 電力拆解：整機（不斷電系統輸出）減去已知元件，以及取樣閘門。

參考專案 PowerBreakdownTests 的對應：
- testEstimatedResidualIncludesMissingCPU、testCompleteBreakdown、testStaleAndSampling、
  testSamplingGateRejectsOldCompletionsAndOverlap：以相同數字移植。
- testMissingAndNegativeResidual：只移植負差值與 NaN 部分；前半段依賴 macOS 的
  「取樣窗已對齊」旗標，Linux 的不斷電系統讀值沒有這個概念。
- testNegativeComponentRejected：移植「負值被拒絕」；差值改依 Linux 規則算成「其他含未取得」。
- testMissingSensorsOnM1：不移植，背光與 ANE 通道只存在於 Apple 晶片；Linux 對應的
  「沒有感測器的元件不算缺值」由 power_service 不傳入該元件處理（見 test_power_service）。
- testUnverifiedSystemWindowDoesNotProduceResidual：不移植，SMC 與 IOReport 取樣窗是 macOS 專屬。
"""
import dataclasses
import math
import unittest

from sdm.constants import Collect, Power
from sdm.core.power_breakdown import (AVAILABLE, INCOMPLETE, INCONSISTENT, SAMPLING, STALE,
                                      SOURCE_COMPONENTS, SOURCE_NONE, SOURCE_UPS, Breakdown,
                                      Component, Item, SamplingGate, make_breakdown)

NOW = 100.0


def gpu(watts, stamp=NOW, index=0):
    return Component(f"gpu:nvidia:{index}", "Example GPU", watts, stamp)


def part(name, watts, stamp=10.0):
    return Component(name, name, watts, stamp)


class Desktop(unittest.TestCase):
    def test_ups_minus_gpu_is_other(self):
        result = make_breakdown(288.0, (gpu(61.5),), NOW)
        self.assertEqual(result.system_w, 288.0)
        self.assertEqual(result.source, SOURCE_UPS)
        self.assertEqual(result.other_w, 226.5)
        self.assertEqual(result.status, AVAILABLE)
        self.assertEqual(result.items, (Item("gpu:nvidia:0", "Example GPU", 61.5),
                                        Item("other", "其他", 226.5)))
        self.assertEqual(result.message, "其他＝整機減去已知元件・含螢幕與電源損耗")
        self.assertEqual(result.total_label, "整機耗電")

    def test_without_ups_total_is_known_parts(self):
        result = make_breakdown(None, (gpu(61.5),), NOW)
        self.assertEqual(result.system_w, 61.5)
        self.assertEqual(result.source, SOURCE_COMPONENTS)
        self.assertEqual(result.status, INCOMPLETE)
        self.assertEqual(result.total_label, "已知元件合計")
        self.assertIsNone(result.other_w)
        self.assertEqual(result.items, (Item("gpu:nvidia:0", "Example GPU", 61.5),))
        self.assertIn("無法取得整機耗電", result.message)

    def test_invalid_system_without_components_is_none(self):
        for value in (math.nan, math.inf, -1.0, Power.SYSTEM_MAX_W, None):
            with self.subTest(value=value):
                result = make_breakdown(value, (), NOW)
                self.assertEqual(result.source, SOURCE_NONE)
                self.assertIsNone(result.system_w)
                self.assertIsNone(result.other_w)
                self.assertEqual(result.items, ())

    def test_invalid_system_falls_back_to_components(self):
        result = make_breakdown(math.nan, (gpu(61.5),), NOW)
        self.assertEqual((result.source, result.system_w), (SOURCE_COMPONENTS, 61.5))

    def test_system_limit_is_exclusive(self):
        self.assertEqual(make_breakdown(1999.9, (), NOW).system_w, 1999.9)
        self.assertEqual(make_breakdown(0.0, (), NOW).other_w, 0.0)

    def test_old_component_is_stale(self):
        limit = Collect.SLOW_INTERVAL * Power.STALE_FACTOR
        fresh = make_breakdown(288.0, (gpu(61.5, stamp=NOW - limit),), NOW)
        self.assertEqual(fresh.status, AVAILABLE)
        result = make_breakdown(288.0, (gpu(61.5, stamp=NOW - limit - 0.1),), NOW)
        self.assertEqual(result.status, STALE)
        self.assertEqual(result.message, "等待新的功耗樣本")
        self.assertEqual((result.system_w, result.other_w, result.items), (288.0, None, ()))

    def test_future_component_is_stale(self):
        self.assertEqual(make_breakdown(288.0, (gpu(61.5, stamp=NOW + 1),), NOW).status, STALE)

    def test_negative_residual_is_inconsistent(self):
        result = make_breakdown(50.0, (gpu(61.5),), NOW)
        self.assertEqual(result.status, INCONSISTENT)
        self.assertIsNone(result.other_w)
        self.assertEqual(result.items, (Item("gpu:nvidia:0", "Example GPU", 61.5),))
        self.assertEqual(result.message, "取樣不同步・暫不計算其他功耗")

    def test_component_at_limit_is_rejected(self):
        result = make_breakdown(288.0, (gpu(Power.COMPONENT_MAX_W),), NOW)
        self.assertIsNone(result.items[0].watts)
        self.assertEqual(result.status, INCOMPLETE)
        self.assertEqual(result.other_w, 288.0)
        self.assertEqual(result.message, "其他包含未取得的元件功耗・差值估算")
        self.assertEqual(make_breakdown(1500.0, (gpu(999.9),), NOW).items[0].watts, 999.9)

    def test_missing_gpu_power_makes_other_include_it(self):
        result = make_breakdown(288.0, (gpu(None), gpu(61.5, index=1)), NOW)
        self.assertEqual(result.other_w, 226.5)
        self.assertEqual(result.status, INCOMPLETE)

    def test_ups_without_components_is_all_other(self):
        result = make_breakdown(288.0, (), NOW)
        self.assertEqual((result.other_w, result.status), (288.0, AVAILABLE))
        self.assertEqual(result.items, (Item("other", "其他", 288.0),))

    def test_sampling_keeps_system_but_no_other(self):
        result = make_breakdown(288.0, (), NOW, sampling=True)
        self.assertEqual((result.status, result.system_w, result.other_w), (SAMPLING, 288.0, None))
        self.assertEqual(result.message, "取樣中・正在讀取元件功耗")

    def test_cpu_slot_is_optional(self):
        self.assertEqual([i.id for i in make_breakdown(288.0, (gpu(61.5),), NOW).items],
                         ["gpu:nvidia:0", "other"])
        cpu = Component("cpu", "處理器", 100.0, NOW)
        result = make_breakdown(288.0, (gpu(61.5),), NOW, cpu=cpu)
        self.assertEqual([i.id for i in result.items], ["gpu:nvidia:0", "cpu", "other"])
        self.assertEqual(result.other_w, 126.5)

    def test_breakdown_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            make_breakdown(288.0, (), NOW).other_w = 1.0
        self.assertEqual(Breakdown().source, SOURCE_NONE)


class PortedReference(unittest.TestCase):
    """參考專案的數字；元件以「背光、CPU、GPU、ANE」四個通用元件表示，取樣間隔 1 秒。"""
    def components(self, backlight=3.0, cpu=2.0, gpu_w=1.0, ane=0.0, stamp=10.0):
        return (part("backlight", backlight, stamp), part("cpu", cpu, stamp),
                part("gpu", gpu_w, stamp), part("ane", ane, stamp))

    def test_estimated_residual_includes_missing_cpu(self):
        sample = self.components(cpu=None)
        result = make_breakdown(12.0, sample, 10.0, interval=1)
        self.assertEqual(result.other_w, 8.0)
        self.assertIsNone(result.items[1].watts)
        self.assertEqual(result.status, INCOMPLETE)
        self.assertIsNone(make_breakdown(2.0, sample, 10.0, interval=1).other_w)

    def test_complete_breakdown(self):
        result = make_breakdown(12.0, self.components(), 10.0, interval=1)
        self.assertEqual(sum(i.watts for i in result.items[1:4]), 3.0)
        self.assertEqual(result.other_w, 6.0)
        self.assertEqual(result.status, AVAILABLE)

    def test_negative_residual_and_nan(self):
        result = make_breakdown(4.0, self.components(), 10.0, interval=1)
        self.assertIsNone(result.other_w)
        self.assertEqual(result.status, INCONSISTENT)
        self.assertIsNone(make_breakdown(math.nan, self.components(), 10.0, interval=1).other_w)

    def test_stale_and_sampling(self):
        result = make_breakdown(12.0, self.components(), 14.0, interval=1)
        self.assertIsNone(result.other_w)
        self.assertEqual(result.items, ())
        self.assertEqual(result.status, STALE)
        sampling = make_breakdown(12.0, self.components(), 10.0, interval=1, sampling=True)
        self.assertEqual(sampling.status, SAMPLING)

    def test_negative_component_rejected(self):
        result = make_breakdown(12.0, self.components(backlight=-1.0), 10.0, interval=1)
        self.assertIsNone(result.items[0].watts)
        self.assertEqual(result.status, INCOMPLETE)


class Gate(unittest.TestCase):
    def test_sampling_gate_rejects_old_completions_and_overlap(self):
        gate = SamplingGate()
        self.assertIsNone(gate.begin())
        gate.set_active(True)
        first = gate.begin()
        self.assertIsNotNone(first)
        self.assertIsNone(gate.begin())
        gate.set_active(False)
        self.assertFalse(gate.finish(first))
        gate.set_active(True)
        following = gate.begin()
        self.assertIsNotNone(following)
        self.assertFalse(gate.finish(first))
        self.assertIsNone(gate.begin())
        self.assertTrue(gate.finish(following))
        self.assertIsNotNone(gate.begin())

    def test_setting_the_same_state_keeps_the_generation(self):
        gate = SamplingGate()
        gate.set_active(True)
        generation = gate.generation
        gate.set_active(True)
        self.assertEqual(gate.generation, generation)
        self.assertTrue(gate.active)


if __name__ == "__main__":
    unittest.main()
