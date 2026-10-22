"""磁碟健康分級、型號清理、內建／外接判斷與 SMART 文字（參考專案 ExternalDiskTests 的數值照搬）。

不照搬：
- testExternalDriveIsListed／testInternalSystemDiskImageAndNetworkAreExcluded 的掛載點與
  DiskArbitration 屬性：Linux 改用 lsblk 的 RM、HOTPLUG、TRAN 判斷（下面有對應案例）；
  磁碟映像（loop）與網路磁碟不是 lsblk 的實體磁碟。
- DiskHealth.parse：diskutil 的 SMARTStatus 文字；Linux 改讀 UDisks2 的 SMART 屬性。
- testLifetimeParsing 的位元組位移、128 位元計數器與 512,000 位元組單位：NVMe 原始記錄頁的格式，
  UDisks2 已換算成位元組；健康度與異常判斷的數值照搬。
"""
import unittest

from sdm.core import disk_health as health
from sdm.core.disk_health import (ATA, NONE, NVME, TONE_CRITICAL, TONE_GOOD, TONE_NORMAL,
                                  TONE_WARNING, DiskSmart)

GB = 1000 ** 3


def nvme(**fields):
    base = dict(kind=NVME, updated=1_800_000_000, critical_warning=(), power_on_hours=2306,
                temperature_c=37.0, percent_used=3, avail_spare=100, spare_thresh=10,
                bytes_read=542 * GB, bytes_written=2_100 * GB, media_errors=0)
    return DiskSmart(**{**base, **fields})


def ata(**fields):
    base = dict(kind=ATA, updated=1_800_000_000, supported=True, enabled=True, failing=False,
                bad_sectors=0, power_on_hours=1180, temperature_c=37.0)
    return DiskSmart(**{**base, **fields})


class Names(unittest.TestCase):
    def test_label_sanitizing(self):
        # ExternalDiskTests.testNonASCIIDigitsAndLabelSanitizing 的 displayName 案例
        self.assertEqual(health.sanitize_model("Photo‮_1T\u0007"), "Photo_1T")
        self.assertEqual(health.sanitize_model("‮\u0000"), "外接磁碟")
        self.assertEqual(len(health.sanitize_model("a" * 200)), 64)
        self.assertEqual(health.sanitize_model("  Example SSD  "), "Example SSD")
        self.assertEqual(health.sanitize_model(None, fallback="磁碟"), "磁碟")

    def test_block_device_names(self):
        # 對應參考專案 isValidDeviceNode：只接受 ASCII 英數字的核心裝置名稱
        for name in ("sda", "nvme0n1", "mmcblk0", "vdb"):
            self.assertTrue(health.valid_block_name(name), name)
        for name in ("", "../sda", "sda/..", "sd a", "sda;rm", "sd٥", "x" * 40, None):
            self.assertFalse(health.valid_block_name(name), name)


class Placement(unittest.TestCase):
    def test_external_drives(self):
        self.assertTrue(health.is_external(rm=False, hotplug=False, tran="usb"))
        self.assertTrue(health.is_external(rm=False, hotplug=False, tran="mmc"))
        self.assertTrue(health.is_external(rm=True, hotplug=False, tran="sata"))
        self.assertTrue(health.is_external(rm=False, hotplug=True, tran=None))

    def test_internal_drives(self):
        self.assertFalse(health.is_external(rm=False, hotplug=False, tran="nvme"))
        self.assertFalse(health.is_external(rm=False, hotplug=False, tran="sata"))
        self.assertFalse(health.is_external(rm=None, hotplug=None, tran=""))

    def test_connection_labels(self):
        cases = {"nvme": "NVMe", "sata": "SATA", "ata": "SATA", "usb": "USB", "mmc": "SD",
                 "": "其他", None: "其他", "fc": "其他"}
        for tran, expected in cases.items():
            self.assertEqual(health.connection_label(tran), expected)


class Bars(unittest.TestCase):
    def test_used_fraction_never_leaves_the_bar(self):
        # 對應 ExternalDiskTests.testUsedSpaceNeverUnderflows
        self.assertEqual(health.bar_fraction(70, 100), 0.7)
        self.assertEqual(health.bar_fraction(130, 100), 1.0)
        self.assertEqual(health.bar_fraction(-5, 100), 0.0)
        self.assertEqual(health.bar_fraction(10, 0), 0.0)

    def test_bar_tones(self):
        self.assertEqual(health.bar_tone(0.91), TONE_CRITICAL)
        self.assertEqual(health.bar_tone(0.9), TONE_WARNING)
        self.assertEqual(health.bar_tone(0.85), TONE_WARNING)
        self.assertEqual(health.bar_tone(0.8), TONE_NORMAL)


class Lifetime(unittest.TestCase):
    def test_health_percent_and_failing(self):
        # testLifetimeParsing：已用 3% → 97%；超過設計壽命 → 0%；嚴重警告或備用區塊低於門檻 → 異常
        self.assertEqual(nvme().health_percent, 97)
        self.assertFalse(nvme().failing_now)
        self.assertEqual(nvme(percent_used=140).health_percent, 0)
        self.assertTrue(nvme(critical_warning=("spare",)).failing_now)
        self.assertTrue(nvme(avail_spare=5, spare_thresh=10).failing_now)
        self.assertIsNone(nvme(percent_used=None).health_percent)

    def test_health_tones(self):
        self.assertEqual(health.health_tone(70), TONE_GOOD)
        self.assertEqual(health.health_tone(69), TONE_WARNING)
        self.assertEqual(health.health_tone(30), TONE_WARNING)
        self.assertEqual(health.health_tone(29), TONE_CRITICAL)

    def test_power_on_text(self):
        self.assertEqual(health.power_on_text(2306), "2306 小時（約 96 天）")
        self.assertEqual(health.power_on_text(47), "47 小時")
        self.assertEqual(health.power_on_text(48), "48 小時（約 2 天）")

    def test_kelvin(self):
        self.assertEqual(round(health.kelvin_to_celsius(310.15)), 37)
        self.assertAlmostEqual(health.kelvin_to_celsius(314), 40.85)
        self.assertIsNone(health.kelvin_to_celsius(0))
        self.assertIsNone(health.kelvin_to_celsius(None))
        self.assertIsNone(health.kelvin_to_celsius(500))


class Summaries(unittest.TestCase):
    def rows(self, smart):
        return dict((name, value) for name, value, _ in health.summarize(smart).lines)

    def test_nvme_with_attributes(self):
        summary = health.summarize(nvme())
        self.assertEqual((summary.badge, summary.tone), ("健康度 97%", TONE_GOOD))
        self.assertEqual(self.rows(nvme()), {
            "健康度": "97%", "通電時間": "2306 小時（約 96 天）", "溫度": "37°C",
            "出廠以來讀取": "542 GB", "出廠以來寫入": "2.1 TB"})

    def test_nvme_problems(self):
        failing = health.summarize(nvme(critical_warning=("temperature",)))
        self.assertEqual((failing.badge, failing.tone), ("異常，建議立即備份", TONE_CRITICAL))
        worn = health.summarize(nvme(percent_used=80))
        self.assertEqual((worn.badge, worn.tone), ("健康度 20%", TONE_CRITICAL))
        self.assertEqual(self.rows(nvme(media_errors=3))["媒體錯誤"], "3 次")
        self.assertNotIn("媒體錯誤", self.rows(nvme()))

    def test_nvme_before_attributes_arrive(self):
        smart = nvme(percent_used=None, avail_spare=None, spare_thresh=None, bytes_read=None,
                     bytes_written=None, media_errors=None, power_on_hours=47)
        summary = health.summarize(smart)
        self.assertEqual((summary.badge, summary.tone), ("SMART 正常", TONE_GOOD))
        self.assertEqual(self.rows(smart), {"通電時間": "47 小時", "溫度": "37°C"})

    def test_ata(self):
        self.assertEqual(health.summarize(ata()).badge, "SMART 正常")
        self.assertEqual(self.rows(ata()), {"通電時間": "1180 小時（約 49 天）", "溫度": "37°C"})
        failing = health.summarize(ata(failing=True))
        self.assertEqual((failing.badge, failing.tone), ("異常，建議立即備份", TONE_CRITICAL))
        sectors = health.summarize(ata(bad_sectors=3))
        self.assertEqual((sectors.badge, sectors.tone), ("有 3 個壞軌", TONE_WARNING))
        self.assertEqual(self.rows(ata(bad_sectors=3))["壞軌"], "3 個")
        self.assertEqual(health.summarize(ata(enabled=False)).badge, "SMART 未啟用")
        self.assertEqual(health.summarize(ata(supported=False)).badge, "SMART 不支援")

    def test_not_updated_unsupported_and_unknown(self):
        self.assertEqual(health.summarize(nvme(updated=0)).badge, "SMART 尚未更新")
        self.assertEqual(health.summarize(ata(updated=0)).badge, "SMART 尚未更新")
        unsupported = health.summarize(DiskSmart(kind=NONE))
        self.assertEqual((unsupported.badge, unsupported.tone, unsupported.lines),
                         ("SMART 不支援", TONE_NORMAL, ()))
        self.assertEqual(health.summarize(None).badge, "讀取中…")


if __name__ == "__main__":
    unittest.main()
