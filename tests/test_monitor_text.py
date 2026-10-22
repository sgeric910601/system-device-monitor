"""系統監控頁的文字：CPU／GPU、記憶體、磁碟與網路（名稱都是虛構的，位址只用 192.0.2.x）。"""
import unittest

from sdm.core.apps import RankedApp
from sdm.core.cpu import CpuSample, CpuUsage
from sdm.core.disk_health import ATA, DiskSmart, TONE_CRITICAL, TONE_NORMAL, TONE_WARNING
from sdm.core.memory import CRITICAL, NORMAL, WARNING, MemoryComposition, Pressure
from sdm.core.snapshots import (CpuDetail, DiskStatus, InterfaceStatus, MemoryDetail,
                                NetworkDetail, VolumeStatus)
from sdm.data.gpu import GpuReading
from sdm.ui import monitor_text as text

GIB = 1024 ** 3
GB = 1000 ** 3
MEMORY = MemoryComposition(total=16 * GIB, app=7 * GIB, shared=GIB // 2, kernel=GIB // 4,
                           cache=4 * GIB, free=2 * GIB, available=6 * GIB, swap_used=GIB,
                           swap_total=8 * GIB, zswap=128 * 1024 ** 2, zswapped=384 * 1024 ** 2)


class Cpu(unittest.TestCase):
    def test_header(self):
        detail = CpuDetail(CpuSample(CpuUsage(0.2, 0.07), ()), (0.75, 1.5, 1.86), 4210.0)
        self.assertEqual(text.cpu_header(detail), ("27%", "使用者 20%・系統 7%"))
        self.assertEqual(text.cpu_header(CpuDetail(None, None, None)), ("—", "計算中…"))
        self.assertEqual(text.cpu_header(None), ("—", "計算中…"))

    def test_load_frequency_and_topology(self):
        self.assertEqual(text.load_text((0.75, 1.5, 1.86)), "0.75／1.50／1.86")
        self.assertEqual(text.load_text(None), "—")
        self.assertEqual(text.frequency_text(4210.0), "4.21 GHz")
        self.assertEqual(text.frequency_text(800.0), "800 MHz")
        self.assertEqual(text.frequency_text(None), "—")
        self.assertEqual(text.topology_text(dict(cores=16, threads=32)), "16 核心・32 執行緒")
        self.assertEqual(text.topology_text({}), "—")

    def test_gpu_rows(self):
        gpu = GpuReading(vendor="nvidia", index=0, name="Example A", usage=12.0,
                         vram_used=3 * GIB, vram_total=24 * GIB, temp_c=45.0, fan_pct=30.0,
                         watts=61.5)
        self.assertEqual(text.gpu_title(gpu), "Example A")
        self.assertEqual(text.gpu_rows(gpu), (("使用率", "12%"), ("顯示記憶體", "3.0 GB / 24 GB"),
                                              ("溫度", "45°C"), ("風扇", "30%"),
                                              ("耗電", "61.5 W")))
        apu = GpuReading(vendor="amd", index=0, name="AMD 內建顯示", integrated=True)
        self.assertEqual(text.gpu_title(apu), "AMD 內建顯示（處理器內建）")
        self.assertEqual(dict(text.gpu_rows(apu))["耗電"], "—")

    def test_app_rows(self):
        ranked = (RankedApp("Example Browser", 153.26, ("a",)), RankedApp("Editor", 2.0, ("b",)))
        self.assertEqual(text.cpu_app_rows(ranked, 32), (
            ("Example Browser", "153.3%", "全機 4.8%"), ("Editor", "2.0%", "全機 0.1%")))
        rows = text.memory_app_rows((RankedApp("Example Browser", 1.8 * GIB, ("a",)),))
        self.assertEqual(rows, (("Example Browser", "1.8 GB", ""),))


class Memory(unittest.TestCase):
    def test_header_and_badge(self):
        detail = MemoryDetail(MEMORY, Pressure(12.0, 0.5), WARNING)
        self.assertEqual(text.memory_header(detail), ("63%", "已使用 10 GB / 16 GB"))
        self.assertEqual(text.pressure_badge(NORMAL), "記憶體壓力：正常")
        self.assertEqual(text.pressure_badge(WARNING), "記憶體壓力：偏高")
        self.assertEqual(text.pressure_badge(CRITICAL), "記憶體壓力：嚴重")
        self.assertEqual(text.memory_header(None), ("—", "讀取中…"))

    def test_legend_swap_and_pressure(self):
        legend = text.memory_legend(MEMORY)
        self.assertEqual([(key, name) for key, name, _ in legend],
                         [("app", "App"), ("shared", "共享"), ("kernel", "核心"),
                          ("cache", "快取"), ("other", "其他"), ("free", "空閒")])
        self.assertEqual(legend[0][2], "7.0 GB")
        self.assertEqual(text.swap_text(MEMORY), "1.0 GB / 8.0 GB")
        self.assertEqual(text.zswap_text(MEMORY), "128 MB（壓縮前 384 MB）")
        no_swap = MemoryComposition(16, 1, 1, 1, 1, 1, 1, 0, 0)
        self.assertEqual(text.swap_text(no_swap), "沒有設定 Swap")
        self.assertIsNone(text.zswap_text(no_swap))
        self.assertEqual(text.pressure_text(Pressure(12.0, 0.5)),
                         "等待記憶體 12.0%（全部停住 0.5%）・近 10 秒")
        self.assertEqual(text.pressure_text(None), "核心沒有提供記憶體壓力資訊")


def disk(**fields):
    base = dict(name="sda", model="Example HDD", connection="SATA", external=False,
                size=4000 * GB, volumes=(VolumeStatus("/srv/example", "ext4", 100 * GB, 95 * GB,
                                                      5 * GB),),
                read_rate=1_234_000.0, write_rate=0.0, read_total=56 * GB, write_total=16 * GB,
                temperature=37.0, smart=DiskSmart(kind=ATA, updated=1, supported=True,
                                                  enabled=True, failing=False, bad_sectors=0))
    return DiskStatus(**{**base, **fields})


class Disks(unittest.TestCase):
    def test_title_and_volume(self):
        self.assertEqual(text.disk_title(disk()), "Example HDD・SATA・4.0 TB")
        volume = disk().volumes[0]
        self.assertEqual(text.volume_title(volume), "/srv/example・EXT4")
        self.assertEqual(text.volume_usage(volume), "已用 95%（可用 5.0 GB／共 100 GB）")
        self.assertEqual(text.volume_tone(volume), TONE_CRITICAL)
        self.assertEqual(text.volume_tone(VolumeStatus("/", "", 100, 85, 15)), TONE_WARNING)
        self.assertEqual(text.volume_tone(VolumeStatus("/", "", 100, 10, 90)), TONE_NORMAL)

    def test_activity_and_temperature(self):
        self.assertEqual(text.disk_rates(disk()), "讀取 1.2 MB/s・寫入 0 B/s")
        self.assertEqual(text.disk_totals(disk()), "讀取 56 GB・寫入 16 GB")
        self.assertEqual(text.disk_temperature(disk()), "37°C")
        self.assertEqual(text.disk_temperature(disk(temperature=None)), "溫度未回報")
        self.assertEqual(text.disk_totals(disk(read_total=None)), "—")


class Network(unittest.TestCase):
    def primary(self):
        return InterfaceStatus("enp1s0", "有線網路", True, 1_200_000.0, 340_000.0,
                               12_400_000_000, 1_900_000_000)

    def test_primary_card(self):
        detail = NetworkDetail(self.primary(), "192.0.2.15", 1_234_000.0, 340_000.0, False, ())
        self.assertEqual(text.network_title(detail), "有線網路・enp1s0")
        self.assertEqual(text.address_text(detail), "IPv4 192.0.2.15")
        self.assertEqual(text.compact_rates(detail), "↓ 1.2M　↑ 340K")
        self.assertEqual(text.network_rows(detail), (("下載", "1.2 MB/s"), ("上傳", "340 KB/s")))
        self.assertEqual(text.network_totals(detail.primary), "下載 12 GB・上傳 1.9 GB")

    def test_disconnected_and_others(self):
        other = InterfaceStatus("wlp2s0", "Wi-Fi", True, None, 0.0, 5 * GB, 0)
        detail = NetworkDetail(None, None, None, None, True, (other,))
        self.assertEqual(text.network_title(detail), "未連線")
        self.assertEqual(text.address_text(detail), "IPv4 —")
        self.assertEqual(text.compact_rates(detail), "↓ —　↑ —")
        self.assertEqual(text.other_rows(detail), (
            ("wlp2s0", "Wi-Fi", "↓ —　↑ 0 B/s", "累計 ↓ 5.0 GB　↑ 0 B"),))


if __name__ == "__main__":
    unittest.main()
