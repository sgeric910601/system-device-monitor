"""溫度分組、保留最近有效值與風扇比例（參考專案 ThermalTests 的數值照搬）。

不照搬：
- testSleepingClusterJunkValuesAreIgnored：Apple 晶片核心群組休眠時回報 0～8.4 °C 的假值，
  Linux 的 k10temp／coretemp 沒有這種情況，所以不另設 20 °C 的下限。
- testKeyTableOnlyCoversVerifiedChips：macOS SMC 溫度鍵對照表；Linux 改用 hwmon 的晶片與標籤分類。
- testIntelFallbackAndPlainSensors 的 firstValid 部分：SMC 備援鍵（-127 代表不存在的核心）；
  plainStatistic 部分照搬。
- 分類案例：參考專案用 IOHID 感測器名稱（PMU tdie、pACC MTR…），這裡改用 hwmon 名稱。
"""
import math
import unittest

from sdm.core import thermal
from sdm.core.thermal import CPU, GPU, NETWORK, OTHER, STORAGE, Fan, Stat, ThermalHold, \
    ThermalReading

T0 = 1_000.0


class Classification(unittest.TestCase):
    def test_linux_sensor_groups(self):
        cases = [(("k10temp", "Tctl"), CPU), (("k10temp", "Tccd1"), CPU),
                 (("coretemp", "Package id 0"), CPU), (("coretemp", "Core 3"), CPU),
                 (("amdgpu", "edge"), GPU), (("amdgpu", "junction"), GPU),
                 (("nvidia", "GPU"), GPU), (("nvme", "Composite"), STORAGE),
                 (("drivetemp", "temp1"), STORAGE), (("acpitz", "temp1"), OTHER),
                 (("coretemp", "Other"), OTHER)]
        for (chip, label), expected in cases:
            with self.subTest(chip=chip, label=label):
                self.assertEqual(thermal.category(chip, label), expected)
        self.assertEqual(thermal.category("r8169_0_100", "temp1", network=True), NETWORK)

    def test_plausible_range(self):
        # ThermalTests.testSensorClassificationAndRangeFilter 的範圍案例
        self.assertFalse(thermal.plausible(0))
        self.assertFalse(thermal.plausible(-3))
        self.assertFalse(thermal.plausible(150))
        self.assertFalse(thermal.plausible(math.nan))
        self.assertTrue(thermal.plausible(41.6))
        self.assertTrue(thermal.plausible(124.9))
        self.assertFalse(thermal.plausible(125))
        self.assertFalse(thermal.plausible(None))
        self.assertFalse(thermal.plausible(True))


def readings(*rows):
    return [ThermalReading(key, group, value) for key, group, value in rows]


class Summary(unittest.TestCase):
    def test_summary_averages_duplicates_and_drops_bad_values(self):
        # ThermalTests.testSummaryAveragesDuplicateServicesAndDropsBadValues；
        # 參考專案「不認得的感測器」在 Linux 一律歸到「其他」，所以沒有對應案例。
        summary = thermal.summary(readings(
            ("Tccd1", CPU, 40), ("Tccd1", CPU, 42),       # 同一個感測器兩個來源先平均成 41
            ("Tccd2", CPU, 45),
            ("Tccd3", CPU, 0),                            # 讀取失敗，丟棄
            ("disk:sda", STORAGE, 34), ("disk:sda", STORAGE, 36)))
        self.assertEqual(summary[CPU], Stat(43, 45))
        self.assertEqual(summary[STORAGE], Stat(35, 35))
        self.assertNotIn(GPU, summary)
        self.assertTrue(summary)
        self.assertEqual(thermal.summary([]), {})

    def test_plain_statistic(self):
        # ThermalTests.testIntelFallbackAndPlainSensors 的 plainStatistic 部分
        self.assertEqual(thermal.statistic([52.1, 50.9, None, 0]).maximum, 52.1)
        self.assertIsNone(thermal.statistic([None, -127]))

    def test_groups_are_listed_in_display_order(self):
        summary = thermal.summary(readings(("x", OTHER, 30), ("n", NETWORK, 50),
                                           ("g", GPU, 60), ("c", CPU, 55)))
        self.assertEqual(list(summary), [CPU, GPU, NETWORK, OTHER])

    def test_text(self):
        self.assertEqual(thermal.stat_text(Stat(43.0, 45.0)), "43°C（最高 45°C）")
        self.assertEqual(thermal.stat_text(Stat(41.2, 41.4)), "41°C")
        self.assertEqual(thermal.stat_text(None), "—")


class Hold(unittest.TestCase):
    def values(self, hold, rows, now):
        return [reading.value for reading in hold.apply(readings(*rows), now)]

    def test_each_sensor_keeps_its_last_valid_value(self):
        # ThermalTests.testEachSensorKeepsItsLastValidValue：時間與有效值照搬；
        # 參考專案的休眠假值（6.7、4.6、5.3）換成 Linux 會遇到的無效讀值（0、讀不到、範圍外）。
        hold = ThermalHold()
        self.assertEqual(self.values(hold, [("A", CPU, 50), ("B", CPU, 60)], T0), [50, 60])
        self.assertEqual(self.values(hold, [("A", CPU, 0), ("B", CPU, 61)], T0 + 1), [50, 61])
        self.assertEqual(self.values(hold, [("A", CPU, None), ("B", CPU, 62)], T0 + 2), [50, 62])
        late = self.values(hold, [("A", CPU, -3), ("B", CPU, 150)], T0 + 122)
        self.assertEqual(late, [62])
        self.assertEqual(self.values(hold, [("A", CPU, 0), ("B", CPU, 0)], T0 + 400), [])
        self.assertEqual(self.values(ThermalHold(), [("X", CPU, 0)], T0), [])

    def test_average_does_not_drop_when_one_sensor_stops_reporting(self):
        # ThermalTests.testAverageDoesNotDropWhenOneClusterSleeps 的數值
        hold = ThermalHold()
        awake = [("Tccd1", CPU, 48), ("Tccd2", CPU, 48), ("Tccd3", CPU, 54), ("Tccd4", CPU, 54)]
        asleep = [("Tccd1", CPU, 48), ("Tccd2", CPU, 48), ("Tccd3", CPU, None),
                  ("Tccd4", CPU, None)]
        self.assertEqual(thermal.summary(hold.apply(readings(*awake), T0))[CPU].average, 51)
        self.assertEqual(thermal.summary(hold.apply(readings(*asleep), T0 + 1))[CPU].average, 51)


class Fans(unittest.TestCase):
    def test_fan_fraction_is_clamped_and_safe_without_max(self):
        # ThermalTests.testFanFractionIsClampedAndSafeWithoutMax（Linux 沒有最低轉速欄位）
        self.assertEqual(Fan("f", rpm=3400, max_rpm=6800).fraction, 0.5)
        self.assertEqual(Fan("f", rpm=9000, max_rpm=6800).fraction, 1)
        self.assertEqual(Fan("f", rpm=3000, max_rpm=0).fraction, 0)

    def test_percent_fans_and_text(self):
        self.assertEqual(Fan("GPU", percent=30.0).fraction, 0.3)
        self.assertEqual(Fan("GPU", percent=130.0).fraction, 1)
        self.assertEqual(Fan("f", rpm=3400, max_rpm=6800).text(), "3400 RPM・50%")
        self.assertEqual(Fan("f", rpm=1150).text(), "1150 RPM")
        self.assertEqual(Fan("GPU", percent=30.0).text(), "30%")
        self.assertFalse(Fan("f", rpm=1150).has_fraction)
        self.assertTrue(Fan("GPU", percent=0.0).has_fraction)


if __name__ == "__main__":
    unittest.main()
