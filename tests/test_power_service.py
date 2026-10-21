"""電源服務：以注入的讀取函式產生桌機耗電快照，並轉成歷史紀錄。"""
import dataclasses
from pathlib import Path
import tempfile
import unittest

from sdm.core.power_breakdown import (AVAILABLE, INCOMPLETE, SAMPLING, SOURCE_COMPONENTS,
                                      SOURCE_UPS, STALE)
from sdm.data.gpu import GpuReading
from sdm.data.machine import read_model, read_uptime
from sdm.data.ups import UpsReading
from sdm.services.power_service import (SUPPLY_MAINS, SUPPLY_UPS_BATTERY, PowerService,
                                        desktop_history_sample, supply_label)

ONLINE = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_pct=32.0,
                    nominal_w=900.0, load_w=288.0, time_left_min=44.2, line_v=118.0,
                    model="Example UPS")
ON_BATTERY = dataclasses.replace(ONLINE, status=("ONBATT",), online=False, on_battery=True,
                                 charge_pct=80.0, load_w=200.0)


def nvidia(watts=61.5, stamp=100.0):
    return GpuReading(vendor="nvidia", index=0, name="Example GPU", watts=watts, stamp=stamp)


def apu(stamp=100.0):
    return GpuReading(vendor="amd", index=0, name="AMD 內建顯示", integrated=True,
                      ppt_raw=20000.0, stamp=stamp)


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class Service(unittest.TestCase):
    def setUp(self):
        self.ups, self.gpus, self.pending = ONLINE, (nvidia(), apu()), False
        self.clock, self.wall = Clock(100.0), Clock(1_800_000_000.0)
        self.service = PowerService(
            read_ups=lambda: self.ups, read_gpus=lambda: self.gpus,
            gpus_pending=lambda: self.pending, processor="Example CPU", model="Example Board",
            read_uptime=lambda: 273600.0, clock=self.clock, wall=self.wall)

    def test_snapshot_fields(self):
        power = self.service.sample()
        self.assertEqual((power.system_w, power.average_w, power.source, power.supply),
                         (288.0, 288.0, SOURCE_UPS, SUPPLY_MAINS))
        self.assertEqual(power.breakdown.status, AVAILABLE)
        self.assertEqual([item.id for item in power.breakdown.items], ["gpu:nvidia:0", "other"])
        self.assertEqual(power.breakdown.other_w, 226.5)
        self.assertEqual(power.gpus, self.gpus)
        self.assertIs(power.ups, ONLINE)
        self.assertEqual((power.processor, power.model, power.uptime_s),
                         ("Example CPU", "Example Board", 273600.0))
        self.assertEqual(power.stamp, 1_800_000_000.0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            power.system_w = 1.0

    def test_integrated_gpu_without_watts_is_not_a_missing_part(self):
        self.gpus = (apu(),)
        power = self.service.sample()
        self.assertEqual((power.breakdown.status, power.breakdown.other_w), (AVAILABLE, 288.0))

    def test_discrete_gpu_without_watts_makes_breakdown_incomplete(self):
        self.gpus = (nvidia(watts=None),)
        self.assertEqual(self.service.sample().breakdown.status, INCOMPLETE)

    def test_average_follows_the_ema(self):
        self.service.sample()
        self.clock.value += 180
        self.ups = dataclasses.replace(ONLINE, load_w=200.0)
        self.gpus = (nvidia(stamp=self.clock.value),)
        self.assertEqual(round(self.service.sample().average_w, 2), 232.37)

    def test_ups_on_battery_changes_the_supply_label(self):
        power = self.service.sample()
        self.assertEqual(supply_label(power.supply), "市電")
        self.ups = ON_BATTERY
        power = self.service.sample()
        self.assertEqual((power.supply, power.system_w), (SUPPLY_UPS_BATTERY, 200.0))
        self.assertEqual(supply_label(power.supply), "不斷電系統電池")

    def test_without_ups_known_parts_and_average_restarts(self):
        self.service.sample()
        self.clock.value += 5
        self.ups = None
        self.gpus = (nvidia(stamp=self.clock.value),)
        power = self.service.sample()
        self.assertEqual((power.source, power.system_w, power.average_w),
                         (SOURCE_COMPONENTS, 61.5, 61.5))
        self.assertEqual(power.supply, SUPPLY_MAINS)

    def test_pending_stream_reports_sampling(self):
        self.pending, self.gpus = True, ()
        self.assertEqual(self.service.sample().breakdown.status, SAMPLING)

    def test_stale_gpu_is_dropped_from_the_list_and_marks_the_breakdown(self):
        self.gpus = (nvidia(stamp=84.9),)
        power = self.service.sample()
        self.assertEqual(power.breakdown.status, STALE)
        self.assertEqual(power.gpus, ())
        self.assertEqual(power.system_w, 288.0)


class HistoryRow(unittest.TestCase):
    def snapshot(self, ups):
        service = PowerService(read_ups=lambda: ups, read_gpus=lambda: (nvidia(),),
                               clock=Clock(100.0), wall=Clock(1_800_000_000.0123),
                               read_uptime=lambda: None)
        return service.sample()

    def test_desktop_sample_from_ups(self):
        sample = desktop_history_sample(self.snapshot(ONLINE))
        self.assertEqual(sample.to_row(), [1_800_000_000.012, None, 288.0, True, False,
                                           100.0, True])

    def test_on_battery_is_unplugged(self):
        sample = desktop_history_sample(self.snapshot(ON_BATTERY))
        self.assertEqual((sample.plugged, sample.ups_online, sample.ups_pct), (False, False, 80.0))

    def test_without_ups_is_plugged_with_known_parts(self):
        sample = desktop_history_sample(self.snapshot(None))
        self.assertEqual(sample.to_row()[1:], [None, 61.5, True, False, None, None])

    def test_comm_lost_counts_as_no_ups(self):
        sample = desktop_history_sample(self.snapshot(UpsReading(status=("COMMLOST",),
                                                                 comm_lost=True)))
        self.assertEqual((sample.plugged, sample.ups_pct, sample.ups_online), (True, None, None))

    def test_nothing_to_record(self):
        service = PowerService(read_ups=lambda: None, read_gpus=lambda: (),
                               clock=Clock(), wall=Clock(), read_uptime=lambda: None)
        self.assertIsNone(desktop_history_sample(service.sample()))
        self.assertIsNone(desktop_history_sample(None))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class Machine(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dmi = self.root/"sys/class/dmi/id"

    def test_vendor_and_product_without_corporate_suffix(self):
        write(self.dmi/"sys_vendor", "Example Technology Co., Ltd.\n")
        write(self.dmi/"product_name", "Board X1\n")
        write(self.dmi/"product_serial", "SERIAL-SENTINEL\n")
        self.assertEqual(read_model(self.root), "Example Board X1")

    def test_product_already_named_after_the_vendor(self):
        write(self.dmi/"sys_vendor", "EXAMPLE\n")
        write(self.dmi/"product_name", "EXAMPLE Laptop 14\n")
        self.assertEqual(read_model(self.root), "EXAMPLE Laptop 14")

    def test_placeholders_and_missing_values(self):
        write(self.dmi/"sys_vendor", "To Be Filled By O.E.M.\n")
        write(self.dmi/"product_name", "Example Board\n")
        self.assertEqual(read_model(self.root), "Example Board")
        write(self.dmi/"product_name", "System Product Name\n")
        self.assertEqual(read_model(self.root), "")
        self.assertEqual(read_model(self.root/"absent"), "")

    def test_long_values_are_capped(self):
        write(self.dmi/"sys_vendor", "Vendor\n")
        write(self.dmi/"product_name", "P" * 200 + "\n")
        self.assertEqual(len(read_model(self.root)), 64)

    def test_uptime(self):
        write(self.root/"proc/uptime", "273600.25 999.00\n")
        self.assertEqual(read_uptime(self.root), 273600.25)
        self.assertIsNone(read_uptime(self.root/"absent"))
        write(self.root/"proc/uptime", "nan 1\n")
        self.assertIsNone(read_uptime(self.root))


if __name__ == "__main__":
    unittest.main()
