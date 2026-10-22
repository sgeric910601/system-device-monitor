"""hwmon 溫度與風扇（假的 /sys）：依硬體路徑把感測器分到 CPU、顯示卡、磁碟、網路卡與其他。"""
from pathlib import Path
import tempfile
import time
import unittest

from sdm.core.thermal import CPU, GPU, NETWORK, OTHER, STORAGE, Fan, summary
from sdm.data import sensors
from sdm.data.gpu import GpuReading
from tests.fixtures import procfs


class Hwmon(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        procfs.write_hwmon(self.root)

    def test_temperatures_keep_the_existing_shape(self):
        found = sensors.hwmon_temperatures(self.root)
        self.assertEqual([(s["chip"], s["label"], s["value"]) for s in found], [
            ("k10temp", "Tctl", 47.125), ("k10temp", "Tccd1", 41.125),
            ("nvme", "Composite", 40.85), ("amdgpu", "edge", 41.0),
            ("r8169_0_100", "temp1", 56.5), ("acpitz", "temp1", 30.0)])
        self.assertTrue(all(Path(s["device"]).is_absolute() for s in found))

    def test_fans_with_and_without_a_maximum(self):
        self.assertEqual(sensors.read_fans(self.root), (
            Fan("amdgpu fan1", rpm=1200.0, max_rpm=3000.0),
            Fan("nct6775 CPU Fan", rpm=900.0, max_rpm=None)))
        self.assertEqual(sensors.hwmon_fans(self.root), [
            dict(name="amdgpu fan1", value=1200.0), dict(name="nct6775 CPU Fan", value=900.0)])

    def test_fans_are_listed_in_file_order(self):
        # 原本依目錄列出的順序（不固定）；改為依檔名排序，畫面上的順序才不會每次跳動
        hwmon = self.root/"sys/class/hwmon/hwmon4"
        (hwmon/"fan3_input").write_text("700\n")
        (hwmon/"fan2_input").write_text("800\n")
        names = [fan.name for fan in sensors.read_fans(self.root) if fan.name.startswith("nct")]
        self.assertEqual(names, ["nct6775 CPU Fan", "nct6775 fan2", "nct6775 fan3"])

    def test_unlabelled_sensors_drop_the_input_suffix(self):
        # 原本沒有標籤檔時顯示「temp1_input」，現在顯示「temp1」
        labels = [s["label"] for s in sensors.hwmon_temperatures(self.root) if s["chip"] == "acpitz"]
        self.assertEqual(labels, ["temp1"])

    def test_readings_are_grouped_by_hardware(self):
        temperatures = sensors.hwmon_temperatures(self.root)
        gpus = (GpuReading(vendor="nvidia", index=0, name="Example A", temp_c=45.0, fan_pct=30.0,
                           stamp=time.monotonic()),
                GpuReading(vendor="amd", index=1, name="AMD 顯示卡", temp_c=41.0, fan_pct=40.0))
        readings = sensors.thermal_readings(temperatures, self.root,
                                            {"nvme0n1": 40.85, "sda": 37.0}, gpus)
        groups = {}
        for reading in readings:
            groups.setdefault(reading.group, []).append(reading.value)
        self.assertEqual(groups, {CPU: [47.125, 41.125], GPU: [41.0, 45.0],
                                  NETWORK: [56.5], OTHER: [30.0], STORAGE: [40.85, 37.0]})
        self.assertEqual(round(summary(readings)[STORAGE].average, 3), 38.925)
        keys = [reading.key for reading in readings if reading.group == STORAGE]
        self.assertEqual(keys, ["disk:nvme0n1", "disk:sda"])

    def test_unreadable_sensors_can_be_listed_as_unknown(self):
        (self.root/"sys/class/hwmon/hwmon0/temp3_input").write_text("garbage\n")
        self.assertEqual([s["label"] for s in sensors.hwmon_temperatures(self.root)
                          if s["chip"] == "k10temp"], ["Tctl"])
        found = sensors.hwmon_temperatures(self.root, include_unreadable=True)
        self.assertEqual([(s["label"], s["value"]) for s in found if s["chip"] == "k10temp"],
                         [("Tctl", 47.125), ("Tccd1", None)])
        self.assertEqual(sensors.storage_temperature([dict(label="Composite", value=None)]), None)
        self.assertEqual(sensors.cpu_temperature([dict(chip="k10temp", label="Tctl", value=None)]),
                         (None, sensors.CPU_MISSING))

    def test_known_disks_without_a_reading_are_listed_as_unknown(self):
        readings = sensors.thermal_readings((), self.root, {"sda": None, "nvme0n1": 41.0}, ())
        self.assertEqual([(r.key, r.value) for r in readings],
                         [("disk:sda", None), ("disk:nvme0n1", 41.0)])

    def test_gpu_fans_come_from_the_nvidia_stream(self):
        gpus = (GpuReading(vendor="nvidia", index=0, name="Example A", fan_pct=30.0),
                GpuReading(vendor="nvidia", index=1, name="Example B"),
                GpuReading(vendor="amd", index=0, name="AMD 顯示卡", fan_pct=40.0))
        self.assertEqual(sensors.gpu_fans(gpus), (Fan("Example A 風扇", percent=30.0),))

    def test_missing_hwmon(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(sensors.hwmon_temperatures(Path(empty)), [])
            self.assertEqual(sensors.read_fans(Path(empty)), ())


class Matching(unittest.TestCase):
    """原本在 collectors 的配對函式搬到 sensors 後行為不變（test_distribution 另有案例）。"""

    def test_cpu_temperature_prefers_tctl(self):
        found = [dict(chip="k10temp", label="Tccd1", value=60.0),
                 dict(chip="k10temp", label="Tctl", value=55.0)]
        self.assertEqual(sensors.cpu_temperature(found), (55.0, "Tctl"))


if __name__ == "__main__":
    unittest.main()
