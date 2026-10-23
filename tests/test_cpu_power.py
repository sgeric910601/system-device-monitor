"""拆解中的 CPU 項目：RAPL 讀值的新鮮度、核心與插槽其餘的下一層、校正過的 PPT 備援，
以及耗電服務、拆解清單與流向圖如何使用它。"""
import dataclasses
import unittest

from sdm.constants import CpuPower
from sdm.core.cpu_power import (CORE_ID, CPU_ID, CPU_LABEL, PPT_LABEL, REST_ID, CpuPowerReading,
                                apu_ppt, rapl_component)
from sdm.core.power_breakdown import AVAILABLE, Item, make_breakdown
from sdm.core.ppt_calibration import PptCalibration
from sdm.data.gpu import GpuReading
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService
from sdm.ui.power_text import breakdown_rows, cpu_watts, flow_model

UPS = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_pct=32.0,
                 nominal_w=900.0, load_w=288.0, time_left_min=44.0, line_v=118.0,
                 model="Example UPS")
NOW = 100.0
INTERVAL = 5                    # 慢速層間隔；新鮮度上限＝3 × 5 秒


def nvidia(stamp=NOW):
    return GpuReading(vendor="nvidia", index=0, name="Example GPU", watts=61.5, stamp=stamp)


def apu(raw=20000.0, stamp=NOW, index=1):
    return GpuReading(vendor="amd", index=index, name="AMD 內建顯示", integrated=True,
                      ppt_raw=raw, stamp=stamp)


def verified():
    calibration = PptCalibration()
    for _ in range(CpuPower.CALIBRATION_SAMPLES):
        calibration.observe(40000.0, 40.0)
    return calibration


class Component(unittest.TestCase):
    def test_fresh_reading_with_core_split(self):
        component = rapl_component(CpuPowerReading(48.6, 31.2, 95.0), NOW, 15)
        self.assertEqual((component.id, component.label, component.watts, component.stamp),
                         (CPU_ID, CPU_LABEL, 48.6, 95.0))
        self.assertEqual(component.children, ((CORE_ID, "核心", 31.2), (REST_ID, "插槽其餘", 17.4)))

    def test_without_core_there_is_no_split(self):
        self.assertEqual(rapl_component(CpuPowerReading(48.6, None, NOW), NOW, 15).children, ())

    def test_core_above_package_is_not_split(self):
        self.assertEqual(rapl_component(CpuPowerReading(20.0, 25.0, NOW), NOW, 15).children, ())

    def test_stale_or_missing_readings(self):
        self.assertIsNone(rapl_component(CpuPowerReading(48.6, None, NOW - 15.1), NOW, 15))
        self.assertIsNone(rapl_component(CpuPowerReading(48.6, None, NOW + 1), NOW, 15))
        self.assertIsNone(rapl_component(None, NOW, 15))

    def test_apu_ppt_is_found_only_when_fresh(self):
        self.assertEqual(apu_ppt((nvidia(), apu()), NOW, 15), (20000.0, NOW))
        self.assertIsNone(apu_ppt((nvidia(),), NOW, 15))
        self.assertIsNone(apu_ppt((apu(stamp=NOW - 16),), NOW, 15))
        self.assertIsNone(apu_ppt((apu(raw=None),), NOW, 15))
        self.assertIsNone(apu_ppt((apu(raw=float("nan")),), NOW, 15))
        self.assertEqual(apu_ppt((apu(raw=5000.0, index=2), apu(raw=7000.0, index=1)), NOW, 15),
                         (7000.0, NOW))


class Service(unittest.TestCase):
    def setUp(self):
        self.cpu = CpuPowerReading(48.6, 31.2, NOW - 2)
        self.gpus = (nvidia(), apu())
        self.calibration = PptCalibration()
        self.service = PowerService(read_ups=lambda: UPS, read_gpus=lambda: self.gpus,
                                    read_uptime=lambda: None, clock=lambda: NOW,
                                    read_cpu=lambda: self.cpu, calibration=self.calibration)

    def test_fresh_rapl_adds_the_cpu_and_updates_other(self):
        breakdown = self.service.sample().breakdown
        self.assertEqual([item.id for item in breakdown.items], ["gpu:nvidia:0", CPU_ID, "other"])
        cpu = breakdown.items[1]
        self.assertEqual((cpu.label, cpu.watts), (CPU_LABEL, 48.6))
        self.assertEqual(cpu.children, (Item(CORE_ID, "核心", 31.2), Item(REST_ID, "插槽其餘", 17.4)))
        self.assertAlmostEqual(breakdown.other_w, 288.0 - 61.5 - 48.6)
        self.assertEqual(breakdown.status, AVAILABLE)

    def test_stale_rapl_is_left_out(self):
        self.cpu = CpuPowerReading(48.6, 31.2, NOW - INTERVAL * 3 - 0.1)
        breakdown = self.service.sample().breakdown
        self.assertEqual([item.id for item in breakdown.items], ["gpu:nvidia:0", "other"])

    def test_rapl_and_ppt_together_feed_the_calibration(self):
        self.cpu = CpuPowerReading(20.0, None, NOW)
        for _ in range(CpuPower.CALIBRATION_SAMPLES):
            self.service.sample()
        self.assertTrue(self.calibration.verified)

    def test_verified_ppt_stands_in_without_rapl(self):
        self.service.calibration = verified()
        self.cpu = None
        cpu = self.service.sample().breakdown.items[1]
        self.assertEqual((cpu.id, cpu.label, cpu.watts, cpu.children),
                         (CPU_ID, PPT_LABEL, 20.0, ()))

    def test_ppt_is_never_cpu_without_verified_calibration(self):
        self.cpu = None
        breakdown = self.service.sample().breakdown
        self.assertNotIn(CPU_ID, [item.id for item in breakdown.items])

    def test_rapl_wins_over_ppt(self):
        self.service.calibration = verified()
        self.assertEqual(self.service.sample().breakdown.items[1].label, CPU_LABEL)

    def test_without_cpu_sources_nothing_changes(self):
        service = PowerService(read_ups=lambda: UPS, read_gpus=lambda: (nvidia(),),
                               read_uptime=lambda: None, clock=lambda: NOW)
        self.assertEqual([item.id for item in service.sample().breakdown.items],
                         ["gpu:nvidia:0", "other"])


def snapshot(cpu=CpuPowerReading(48.6, 31.2, NOW)):
    service = PowerService(read_ups=lambda: UPS, read_gpus=lambda: (nvidia(),),
                           read_uptime=lambda: None, clock=lambda: NOW, read_cpu=lambda: cpu)
    return service.sample()


class Text(unittest.TestCase):
    def test_children_are_indented_rows_under_the_cpu(self):
        rows = breakdown_rows(snapshot().breakdown)
        self.assertEqual(rows[1:4], (("cpu", "CPU", "48.6 W", "17%"),
                                     ("cpu:core", "　核心", "31.2 W", "11%"),
                                     ("cpu:rest", "　插槽其餘", "17.4 W", "6%")))
        self.assertEqual(rows[-1][0], "other")

    def test_hiding_the_cpu_hides_its_children(self):
        ids = [row[0] for row in breakdown_rows(snapshot().breakdown, ("cpu",))]
        self.assertEqual(ids, ["gpu:nvidia:0", "other"])

    def test_flow_has_only_top_level_items(self):
        self.assertEqual([item[0] for item in flow_model(snapshot()).items],
                         ["gpu:nvidia:0", "cpu", "other"])

    def test_cpu_watts_for_the_ranking(self):
        self.assertEqual(cpu_watts(snapshot()), 48.6)
        self.assertIsNone(cpu_watts(snapshot(cpu=None)))
        self.assertIsNone(cpu_watts(None))
        empty = dataclasses.replace(snapshot(), breakdown=make_breakdown(None, (), NOW))
        self.assertIsNone(cpu_watts(empty))


class Wiring(unittest.TestCase):
    """收集器把客戶端與校正交給耗電服務；模擬測試不讀寫校正檔；示範模式有示範的 CPU。"""

    def test_calibration_is_stored_in_the_state_directory(self):
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from sdm import paths
        from sdm.data.collectors import calibration_store
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(paths, "STATE", Path(temp)/"state"):
                calibration = calibration_store(persist=True)
                for _ in range(CpuPower.CALIBRATION_SAMPLES):
                    calibration.observe(40000.0, 40.0)
                stored = Path(temp)/"state"/CpuPower.CALIBRATION_FILE
                self.assertTrue(json.loads(stored.read_text())["ppt"]["verified"])
                self.assertTrue(calibration_store(persist=True).verified)
                self.assertFalse(calibration_store(persist=False).verified)
                stored.unlink()
                memory = calibration_store(persist=False)
                for _ in range(CpuPower.CALIBRATION_SAMPLES):
                    memory.observe(40000.0, 40.0)
                self.assertFalse(stored.exists())

    def test_power_service_reads_the_client(self):
        from sdm.data.collectors import Collectors
        from sdm.data.cpu_power_client import CpuPowerClient
        collector = Collectors.__new__(Collectors)
        collector.nvidia = SimpleStream()
        collector.bus, collector.cpu_info = None, dict(name="Example CPU")
        collector.cpu_power, collector.calibration = CpuPowerClient(), PptCalibration()
        service = collector.make_power_service(model="Example Board")
        self.assertEqual(service.read_cpu, collector.cpu_power.latest)
        self.assertIs(service.calibration, collector.calibration)

    def test_preview_desktop_shows_a_demo_cpu(self):
        from sdm.services.preview import DESKTOP, power_service
        breakdown = power_service(DESKTOP).sample().breakdown
        cpu = next(item for item in breakdown.items if item.id == CPU_ID)
        self.assertEqual(cpu.label, CPU_LABEL)
        self.assertEqual([child.id for child in cpu.children], [CORE_ID, REST_ID])


class SimpleStream:
    def latest(self):
        return ()

    def pending(self):
        return False


if __name__ == "__main__":
    unittest.main()
