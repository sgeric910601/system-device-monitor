"""各頁的詳細取樣：只讀被要求的部分，第二次取樣才有速率（假的 /proc、/sys 與 SMART）。"""
from pathlib import Path
import tempfile
import time
import unittest

from sdm.core.disk_health import ATA, DiskSmart
from sdm.core.memory import CRITICAL, WARNING
from sdm.core.thermal import CPU, GPU, NETWORK, STORAGE
from sdm.core.tiers import DetailRequest, request_for
from sdm.data.detail import DetailCollector
from sdm.data.gpu import GpuReading
from tests.fixtures import procfs

GIB = 1024 ** 3
DISKS = [dict(name="nvme0n1", model="Example SSD", transport="nvme", size=2 * GIB,
              connection="NVMe", external=False,
              volumes=[dict(mount="/", fstype="ext4", total=100, used=95, available=5)]),
         dict(name="sda", model="Example HDD", transport="sata", size=4 * GIB,
              connection="SATA", external=False, volumes=[])]


class FakeSmart:
    def __init__(self):
        self.refreshed = []
        self.values = {"sda": DiskSmart(kind=ATA, updated=1, supported=True, enabled=True,
                                        failing=False, bad_sectors=0, temperature_c=37.0)}
    def refresh(self, names, attributes=False):
        self.refreshed.append((tuple(names), attributes))
        return {name: self.values[name] for name in names if name in self.values}
    def cached(self, name):
        return self.values.get(name)


class Detail(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        procfs.write_system(self.root)
        procfs.write_hwmon(self.root)
        procfs.write_cgroups(self.root)
        self.clock = [100.0]
        self.smart = FakeSmart()
        self.gpus = (GpuReading(vendor="nvidia", index=0, name="Example A", temp_c=45.0,
                                fan_pct=30.0, stamp=time.monotonic()),)
        self.addresses = []
        def address(name):
            self.addresses.append(name)
            return "192.0.2.15"
        self.collector = DetailCollector(
            root=self.root, smart=self.smart, disk_meta=lambda: DISKS,
            gpus=lambda: self.gpus, uid=1000, clock=lambda: self.clock[0], address=address)

    def second(self, request):
        self.collector.sample(request)
        procfs.write_system(self.root, step=1)
        procfs.write_cgroups(self.root, step=1)
        self.clock[0] += 2
        return self.collector.sample(request)

    def test_only_requested_parts_are_sampled(self):
        snapshot = self.collector.sample(DetailRequest(memory=True))
        self.assertIsNotNone(snapshot.memory)
        self.assertEqual((snapshot.cpu, snapshot.thermal, snapshot.apps, snapshot.disks,
                          snapshot.network), (None,) * 5)
        self.assertEqual(self.smart.refreshed, [])

    def test_cpu_page(self):
        snapshot = self.second(request_for({"cpu"}))
        cpu = snapshot.cpu
        self.assertAlmostEqual(cpu.sample.total.total, 0.25)
        self.assertEqual(len(cpu.sample.cores), 2)
        self.assertEqual(cpu.load, (0.75, 1.53, 1.86))
        self.assertEqual(cpu.frequency_mhz, 4000.0)
        groups = dict(snapshot.thermal.groups)
        self.assertEqual(set(groups), {CPU, GPU, STORAGE, NETWORK, "other"})
        self.assertEqual(groups[GPU].maximum, 45.0)
        self.assertEqual(groups[STORAGE].maximum, 40.85)     # nvme0n1（hwmon）與 sda（SMART）
        self.assertEqual([fan.name for fan in snapshot.thermal.fans],
                         ["amdgpu fan1", "nct6775 CPU Fan", "Example A 風扇"])
        usages = {usage.app_id: usage for usage in snapshot.apps.sample.usages}
        self.assertAlmostEqual(usages["example-browser"].core_percent, 50.0)
        self.assertAlmostEqual(usages["@session"].core_percent, 5.0)
        self.assertIsNone(usages["example-browser"].memory)   # CPU 頁不讀記憶體
        self.assertEqual((snapshot.apps.has_cpu, snapshot.apps.has_memory), (True, False))

    def test_a_disk_temperature_that_stops_reporting_is_held(self):
        request = DetailRequest(thermal=True)
        first = dict(self.collector.sample(request).thermal.groups)[STORAGE]
        self.assertAlmostEqual(first.average, (40.85 + 37.0) / 2)
        self.smart.values.clear()                       # SATA 硬碟這次讀不到溫度
        self.clock[0] += 5
        held = dict(self.collector.sample(request).thermal.groups)[STORAGE]
        self.assertAlmostEqual(held.average, (40.85 + 37.0) / 2)

    def test_thermal_is_read_every_five_seconds(self):
        request = DetailRequest(thermal=True)
        first = self.collector.sample(request).thermal
        self.clock[0] += 2
        self.assertIs(self.collector.sample(request).thermal, first)
        self.clock[0] += 3
        self.assertIsNot(self.collector.sample(request).thermal, first)

    def test_memory_page(self):
        snapshot = self.collector.sample(request_for({"memory"}))
        memory = snapshot.memory
        self.assertEqual(memory.composition.used, 10 * GIB)
        self.assertEqual((memory.pressure.some_avg10, memory.level), (12.0, WARNING))
        usages = {usage.app_id: usage.memory for usage in snapshot.apps.sample.usages}
        self.assertEqual(usages["example-browser"], 300 * 1024 ** 2)
        self.assertEqual((snapshot.apps.has_cpu, snapshot.apps.has_memory), (False, True))

    def test_disk_page(self):
        snapshot = self.second(request_for({"disk"}))
        disks = {disk.name: disk for disk in snapshot.disks}
        self.assertEqual((disks["nvme0n1"].read_rate, disks["nvme0n1"].write_rate), (512.0, 1024.0))
        self.assertEqual((disks["nvme0n1"].read_total, disks["nvme0n1"].write_total),
                         (1002 * 512, 2004 * 512))
        self.assertEqual(disks["nvme0n1"].temperature, 40.85)
        self.assertEqual(disks["sda"].temperature, 37.0)
        self.assertEqual(disks["sda"].smart.kind, ATA)
        self.assertIsNone(disks["nvme0n1"].smart)
        self.assertEqual(disks["nvme0n1"].volumes[0].fstype, "ext4")
        self.assertEqual(self.smart.refreshed[-1], (("nvme0n1", "sda"), True))

    def test_slow_smart_calls_do_not_distort_disk_rates(self):
        """SMART 呼叫卡住 5 秒、期間磁碟仍在讀寫：速率要用讀計數器當下的時間計算。"""
        root, clock = self.root, self.clock
        class SlowSmart(FakeSmart):
            def refresh(self, names, attributes=False):
                if not self.refreshed:
                    procfs.write_system(root, step=1)
                    clock[0] += 5
                return super().refresh(names, attributes)
        self.collector.smart = SlowSmart()
        request = request_for({"disk"})
        self.collector.sample(request)                 # t=100 讀到第一次的計數器
        self.clock[0] += 2                             # 第二次取樣在 t=107
        disks = {disk.name: disk for disk in self.collector.sample(request).disks}
        self.assertAlmostEqual(disks["nvme0n1"].read_rate, 2 * 512 / 7)
        self.assertAlmostEqual(disks["nvme0n1"].write_rate, 4 * 512 / 7)

    def test_each_counter_uses_the_time_it_was_read(self):
        stamps = []
        clock = self.collector.clock
        def ticking():
            stamps.append(clock())
            return stamps[-1]
        self.collector.clock = ticking
        self.collector.sample(request_for({"cpu", "network", "disk"}))
        self.assertGreaterEqual(len(stamps), 4)    # 開始、CPU、App、網路、磁碟各讀一次時鐘

    def test_overview_only_refreshes_smart(self):
        snapshot = self.collector.sample(request_for({"overview"}))
        self.assertIsNone(snapshot.disks)
        self.assertEqual(self.smart.refreshed, [(("nvme0n1", "sda"), False)])

    def test_network_page(self):
        snapshot = self.second(request_for({"network"}))
        network = snapshot.network
        self.assertEqual((network.primary.name, network.primary.kind), ("enp1s0", "有線網路"))
        self.assertEqual((network.rx_rate, network.tx_rate), (1000.0, 500.0))
        self.assertEqual((network.primary.rx_total, network.primary.tx_total), (7000, 4000))
        self.assertEqual(network.address, "192.0.2.15")
        self.assertFalse(network.changed)
        self.assertEqual([item.name for item in network.others], ["wlp2s0", "veth1"])
        self.assertEqual(self.addresses, ["enp1s0"])          # 30 秒內不重讀

    def test_network_without_a_default_route(self):
        (self.root/"proc/net/route").write_text("Iface\tDestination\n")
        network = self.collector.sample(DetailRequest(network=True)).network
        self.assertEqual((network.primary, network.address, network.rx_rate), (None, None, None))
        self.assertEqual(len(network.others), 3)


class Pressure(unittest.TestCase):
    def test_critical_when_everything_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            procfs.write_system(root)
            (root/"proc/pressure/memory").write_text(
                "some avg10=20.00 avg60=0 avg300=0 total=1\nfull avg10=6.00 avg60=0 avg300=0 total=1\n")
            collector = DetailCollector(root=root, uid=1000)
            self.assertEqual(collector.sample(DetailRequest(memory=True)).memory.level, CRITICAL)
            (root/"proc/pressure/memory").unlink()
            memory = collector.sample(DetailRequest(memory=True)).memory
            self.assertIsNone(memory.pressure)


if __name__ == "__main__":
    unittest.main()
