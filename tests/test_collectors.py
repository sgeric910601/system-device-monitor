"""收集器的快速取樣（system）、慢速取樣（slow）與磁碟清單：拆分前後行為必須相同。

所有 /proc 與 /sys 都指向暫存目錄裡的假檔案；介面與磁碟名稱、型號都是虛構的。
"""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from sdm.core.disk_health import ATA, DiskSmart
from sdm.core.tiers import DetailRequest
from sdm.data.collectors import Collectors
from sdm.data.gpu import GpuReading
from tests.fixtures import procfs

GIB = 1024 ** 3
REAL_MONOTONIC = time.monotonic     # 耗電服務用真正的時鐘判斷顯示卡讀值是否過期


class FakeStream:
    def __init__(self, readings=()):
        self.values = tuple(readings)
    def latest(self): return self.values
    def readings(self, now=None): return self.values
    def pending(self, now=None): return False


class Fixture(unittest.TestCase):
    """建立假的 /proc、/sys，並把收集器讀取的絕對路徑改指到暫存目錄。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        procfs.write_system(self.root)
        root = self.root

        def mapped(path):
            text = str(path)
            if text.startswith(("/sys/", "/proc/")):
                return root/text.lstrip("/")
            return Path(path)

        def fake_read(path, default=""):
            try:
                return mapped(path).read_text().strip()
            except (OSError, UnicodeError):
                return default

        for target, replacement in (("sdm.data.collectors.Path", mapped),
                                    ("sdm.data.collectors.read", fake_read)):
            patcher = patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = [100.0]
        patcher = patch("sdm.data.collectors.time.monotonic", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.collector = Collectors.__new__(Collectors)
        from sdm.core.counters import Counters, CpuCounter
        self.collector.cpu, self.collector.rates = CpuCounter(), Counters()
        self.collector.cpu_info = dict(name="Example CPU", cores=4, threads=8)
        self.collector.bus = None
        self.collector.root = self.root


class SystemSample(Fixture):
    def test_first_sample_has_no_rates(self):
        result = self.collector.system()
        self.assertIsNone(result["cpu"])
        self.assertAlmostEqual(result["ram"], 62.5)
        self.assertEqual((result["ram_used"], result["ram_total"]), (10 * GIB, 16 * GIB))
        self.assertEqual([n["name"] for n in result["networks"]], ["enp1s0", "wlp2s0", "veth1"])
        self.assertTrue(all(n["rx"] is None and n["tx"] is None for n in result["networks"]))
        self.assertIsNone(result["rx"])
        self.assertEqual([d["name"] for d in result["disks"]], ["nvme0n1", "sda"])
        self.assertEqual(result["cpu_info"]["name"], "Example CPU")

    def test_second_sample_gives_rates_and_physical_totals(self):
        self.collector.system()
        procfs.write_system(self.root, step=1)
        self.clock[0] += 2
        result = self.collector.system()
        self.assertAlmostEqual(result["cpu"], 25.0)
        networks = {n["name"]: n for n in result["networks"]}
        self.assertEqual({name: n["kind"] for name, n in networks.items()},
                         {"enp1s0": "有線網路", "wlp2s0": "Wi-Fi", "veth1": "虛擬介面"})
        self.assertEqual((networks["enp1s0"]["rx"], networks["enp1s0"]["tx"]), (1000.0, 500.0))
        self.assertEqual((networks["wlp2s0"]["rx"], networks["wlp2s0"]["tx"]), (2000.0, 0.0))
        self.assertFalse(networks["veth1"]["physical"])
        # 網速合計只加實體介面
        self.assertEqual((result["rx"], result["tx"]), (3000.0, 500.0))
        disks = {d["name"]: d for d in result["disks"]}
        self.assertEqual((disks["nvme0n1"]["read"], disks["nvme0n1"]["write"]), (512.0, 1024.0))
        self.assertEqual((disks["sda"]["read"], disks["sda"]["write"]), (0.0, 0.0))


class SlowSample(Fixture):
    def setUp(self):
        super().setUp()
        nvme = self.root/"sys/devices/pci0/nvme/nvme0"
        nic = self.root/"sys/devices/pci0/nic"
        (nvme).mkdir(parents=True)
        (nic/"mdio").mkdir(parents=True)
        block = self.root/"sys/class/block/nvme0n1"
        block.mkdir(parents=True, exist_ok=True)
        (block/"device").symlink_to(nvme)
        (self.root/"sys/class/net/enp1s0/device").rmdir()
        (self.root/"sys/class/net/enp1s0/device").symlink_to(nic)
        self.sensors = [
            dict(chip="k10temp", label="Tctl", value=55.0, device=str(self.root/"sys/devices/cpu")),
            dict(chip="nvme", label="Composite", value=41.0, device=str(nvme)),
            dict(chip="nvme", label="Sensor 1", value=48.0, device=str(nvme)),
            dict(chip="r8169_0", label="temp1", value=50.0, device=str(nic/"mdio")),
            dict(chip="acpitz", label="temp1", value=30.0, device=str(self.root/"sys/devices/acpi")),
        ]
        self.collector.nvidia = FakeStream((GpuReading(vendor="nvidia", index=0, name="Example A",
                                                       usage=5.0, stamp=REAL_MONOTONIC()),))
        self.collector.power = self.collector.make_power_service(model="Example Board")
        self.collector.disk_meta = [dict(name="nvme0n1", model="Example SSD", transport="nvme",
                                         size=GIB, volumes=[])]
        self.collector.last_disk_meta = self.clock[0]

    def sample(self):
        with patch("sdm.data.collectors.hwmon_temperatures", return_value=self.sensors), \
                patch("sdm.data.collectors.hwmon_fans", return_value=[]), \
                patch("sdm.data.collectors.command", side_effect=OSError("no ups")), \
                patch("sdm.data.collectors.read_amd_gpus", return_value=()), \
                patch("sdm.data.collectors.read_batteries", return_value=()), \
                patch("sdm.data.collectors.read_adapters", return_value=()):
            return self.collector.slow()

    def test_temperatures_are_matched_by_hardware_path(self):
        result = self.sample()
        self.assertEqual((result["cpu_temp"], result["cpu_temp_label"]), (55.0, "Tctl"))
        self.assertEqual([(d["name"], d["temperature"]) for d in result["storage"]],
                         [("nvme0n1", 41.0)])
        self.assertEqual(result["network_temperatures"], {"enp1s0": 50.0})
        self.assertEqual([s["chip"] for s in result["temperatures"]], ["acpitz"])
        self.assertEqual(result["gpu"].name, "Example A")
        self.assertEqual(result["fans"], [])

    def test_smart_temperature_fills_the_gap(self):
        self.collector.disk_meta = [dict(name="sda", model="Example HDD", transport="sata",
                                         size=GIB, volumes=[])]
        self.collector.smart = FakeSmart({"sda": DiskSmart(kind=ATA, temperature_c=37.0)})
        result = self.sample()
        self.assertEqual([(d["name"], d["temperature"]) for d in result["storage"]],
                         [("sda", 37.0)])

    def test_disk_without_a_sensor_has_no_temperature(self):
        self.collector.disk_meta = [dict(name="sda", model="Example HDD", transport="sata",
                                         size=GIB, volumes=[])]
        result = self.sample()
        self.assertEqual([(d["name"], d["temperature"]) for d in result["storage"]],
                         [("sda", None)])
        self.assertEqual(sorted(s["chip"] for s in result["temperatures"]),
                         ["acpitz", "nvme", "nvme"])


class FakeSmart:
    def __init__(self, values):
        self.values = values
    def cached(self, name):
        return self.values.get(name)


class DetailMethod(Fixture):
    def test_detail_samples_the_requested_pages(self):
        from sdm.data.detail import DetailCollector
        self.collector.details = DetailCollector(root=self.root, uid=1000)
        snapshot = self.collector.detail(DetailRequest(memory=True))
        self.assertEqual(snapshot.memory.composition.total, 16 * GIB)
        self.assertIsNone(snapshot.cpu)

    def test_collector_builds_its_detail_sampler(self):
        from gi.repository import GLib
        with patch("sdm.data.collectors.Gio.bus_get_sync", side_effect=GLib.Error("none")), \
                patch("sdm.data.collectors.load_json", return_value={}):
            collector = Collectors()
        self.assertIs(collector.details.smart, collector.smart)
        self.assertEqual(collector.details.root, collector.root)


class StatvfsResult:
    f_blocks, f_frsize, f_bfree, f_bavail = 1000, 4096, 400, 300


class DiskMeta(unittest.TestCase):
    def lsblk(self):
        return json.dumps({"blockdevices": [
            {"name": "nvme0n1", "type": "disk", "tran": "nvme", "size": 2 * GIB, "fstype": None,
             "mountpoints": [None], "model": " Example SSD ", "children": [
                 {"name": "nvme0n1p1", "type": "part", "tran": "nvme", "size": GIB,
                  "fstype": "ext4", "mountpoints": ["/"], "model": None},
                 {"name": "nvme0n1p2", "type": "part", "tran": "nvme", "size": GIB,
                  "fstype": "squashfs", "mountpoints": ["/snap/example/1"], "model": None}]},
            {"name": "loop0", "type": "loop", "size": 1, "mountpoints": ["/snap/x"]},
            {"name": "zram0", "type": "disk", "size": 1, "mountpoints": ["[SWAP]"]},
        ]})

    def test_physical_disks_and_mounted_volumes(self):
        collector = Collectors.__new__(Collectors)
        collector.last_disk_meta = 0
        with patch("sdm.data.collectors.command", return_value=self.lsblk()) as run, \
                patch("sdm.data.collectors.os.statvfs", return_value=StatvfsResult()):
            collector.refresh_disk_meta()
        self.assertIn("/usr/bin/lsblk", run.call_args.args[0])
        self.assertIn("NAME,TYPE,TRAN,SIZE,FSTYPE,MOUNTPOINTS,MODEL,RM,HOTPLUG",
                      run.call_args.args[0])
        self.assertEqual(collector.disk_meta, [dict(
            name="nvme0n1", model="Example SSD", transport="nvme", size=2 * GIB,
            connection="NVMe", external=False,
            volumes=[dict(mount="/", fstype="ext4", total=4096000, used=2457600,
                          available=1228800)])])

    def test_cadence_depends_on_whether_disks_are_shown(self):
        collector = Collectors.__new__(Collectors)
        collector.last_disk_meta = time.monotonic() - 60
        with patch("sdm.data.collectors.command", return_value=self.lsblk()) as run, \
                patch("sdm.data.collectors.os.statvfs", return_value=StatvfsResult()):
            collector.storage_visible = False
            collector.refresh_disk_meta()
            run.assert_not_called()             # 沒有顯示磁碟時每 5 分鐘
            collector.storage_visible = True
            collector.refresh_disk_meta()
            run.assert_called_once()            # 總覽或磁碟頁顯示時每 30 秒

    def test_failure_clears_the_list_and_cadence_is_respected(self):
        collector = Collectors.__new__(Collectors)
        collector.last_disk_meta, collector.disk_meta = 0, ["old"]
        with patch("sdm.data.collectors.command", side_effect=OSError("missing")):
            collector.refresh_disk_meta()
        self.assertEqual(collector.disk_meta, [])
        with patch("sdm.data.collectors.command") as run:
            collector.refresh_disk_meta()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
