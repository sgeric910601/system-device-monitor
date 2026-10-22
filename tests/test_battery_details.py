"""筆電詳細資訊的 18 個欄位：Linux 對應、排列順序、隱藏設定與讀不到的欄位。"""
import dataclasses
from pathlib import Path
import tempfile
import unittest

from sdm.core.battery import combine
from sdm.core.estimates import SystemTimes, estimate
from sdm.core.power_breakdown import Breakdown
from sdm.core.preference_specs import NAME_PATTERN
from sdm.data.power_supply import read_adapters, read_batteries
from sdm.services.battery_service import LaptopPower
from sdm.ui.battery_details import DETAIL_FIELDS, available_fields, detail_rows
from tests.fixtures import sysfs


def laptop_from(builder, system=SystemTimes(), profile=None, **options):
    with tempfile.TemporaryDirectory() as temp:
        builder(Path(temp), **options)
        snapshot = combine(read_batteries(Path(temp)), read_adapters(Path(temp)))
    return LaptopPower(stamp=0.0, battery=snapshot, estimates=estimate(snapshot, system=system),
                       breakdown=Breakdown(), profile=profile)


class Fields(unittest.TestCase):
    def test_reference_order_and_valid_preference_ids(self):
        self.assertEqual([field_id for field_id, _ in DETAIL_FIELDS], [
            "voltage", "current", "battery_power", "system_power", "system_estimate",
            "temperature", "health", "computed_health", "cycles", "condition", "remaining",
            "full_charge", "design", "low_power", "adapter", "charge_limit", "adapter_voltage",
            "adapter_current"])
        self.assertTrue(all(NAME_PATTERN.fullmatch(field_id) for field_id, _ in DETAIL_FIELDS))

    def test_limit_shown_elsewhere_moves_low_power_last(self):
        ids = [field_id for field_id, _ in available_fields(True)]
        self.assertNotIn("charge_limit", ids)
        self.assertEqual(ids[-1], "low_power")
        self.assertEqual(ids[-4:-1], ["adapter", "adapter_voltage", "adapter_current"])
        self.assertEqual(available_fields(False), DETAIL_FIELDS)


class Values(unittest.TestCase):
    def test_thinkpad_on_battery(self):
        power = laptop_from(sysfs.thinkpad, SystemTimes(reports=True, to_empty_min=205),
                            "balanced")
        self.assertEqual(detail_rows(power), (
            ("voltage", "電池電壓", "12.45 V"), ("current", "電池電流", "-1,205 mA"),
            ("battery_power", "電池功率", "15.0 W"), ("system_power", "系統耗電", "15.0 W"),
            ("system_estimate", "系統預估", "3 小時 25 分"),
            ("temperature", "電池溫度", "31.4 °C"), ("health", "健康度", "91%"),
            ("computed_health", "容量推算", "90.9%"), ("cycles", "循環次數", "212"),
            ("condition", "電池狀況", "正常"),
            ("remaining", "剩餘容量", "41.0 Wh（3,550 mAh）"),
            ("full_charge", "滿充容量", "50.0 Wh（4,329 mAh）"),
            ("design", "設計容量", "55.0 Wh（4,762 mAh）"),
            ("adapter", "變壓器", "未連接"), ("adapter_voltage", "變壓器電壓", "未連接"),
            ("adapter_current", "變壓器電流", "未連接"), ("low_power", "低耗電模式", "關閉")))

    def test_hidden_preferences_are_respected(self):
        power = laptop_from(sysfs.thinkpad)
        ids = [row[0] for row in detail_rows(power, hidden=("temperature", "cycles"))]
        self.assertNotIn("temperature", ids)
        self.assertNotIn("cycles", ids)
        self.assertEqual(detail_rows(power, hidden=[field for field, _ in DETAIL_FIELDS]), ())

    def test_unavailable_values_are_hidden(self):
        power = laptop_from(sysfs.thinkpad, online=1, status="Charging", power_now=20_000_000)
        ids = [row[0] for row in detail_rows(power)]
        for missing in ("system_power", "system_estimate", "low_power", "adapter_voltage",
                        "adapter_current"):
            self.assertNotIn(missing, ids)
        self.assertIn(("adapter", "變壓器", "已連接"), detail_rows(power))

    def test_framework_adapter_and_charge_counters(self):
        power = laptop_from(sysfs.framework, profile="power-saver")
        rows = {row[0]: row[2] for row in detail_rows(power)}
        self.assertEqual((rows["adapter"], rows["adapter_voltage"], rows["adapter_current"]),
                         ("協商 45 W／額定 65 W", "20.00 V（協商）", "2,250 mA（協商）"))
        self.assertNotIn("system_power", rows)
        self.assertEqual(rows["remaining"], "30.4 Wh（1,965 mAh）")
        self.assertEqual((rows["current"], rows["low_power"]), ("1,500 mA", "開啟"))
        self.assertNotIn("charge_limit", rows)

    def test_charge_limit_without_a_limit_set(self):
        power = laptop_from(sysfs.thinkpad, charge_control_end_threshold=100)
        rows = [row[0] for row in detail_rows(power, hidden=())]
        self.assertIn("charge_limit", rows)
        self.assertEqual(dict((row[0], row[2]) for row in detail_rows(power))["charge_limit"],
                         "未設定")
        self.assertLess(rows.index("low_power") if "low_power" in rows else 0,
                        rows.index("charge_limit"))
        no_file = laptop_from(sysfs.no_thresholds)
        self.assertNotIn("charge_limit", [row[0] for row in detail_rows(no_file)])

    def test_condition_prefers_the_health_attribute(self):
        power = laptop_from(sysfs.thinkpad, health="Overheat")
        self.assertIn(("condition", "電池狀況", "過熱"), detail_rows(power))
        unknown = laptop_from(sysfs.thinkpad, capacity_level="Unknown")
        self.assertNotIn("condition", [row[0] for row in detail_rows(unknown)])

    def test_system_estimate_while_charging_and_plugged(self):
        charging = laptop_from(sysfs.asus, SystemTimes(reports=True, to_full_min=96))
        values = {row[0]: row[2] for row in detail_rows(charging)}
        # 有上限時顯示到上限的時間：96 分鐘充滿 × (80 − 64) ÷ (100 − 64) = 42 分鐘
        self.assertEqual(values["system_estimate"], "到 80% 0:42")
        estimating = laptop_from(sysfs.asus, SystemTimes(reports=True))
        values = {row[0]: row[2] for row in detail_rows(estimating)}
        self.assertEqual(values["system_estimate"], "計算中…")
        no_limit = laptop_from(sysfs.asus, SystemTimes(reports=True, to_full_min=96),
                               charge_control_end_threshold=100)
        values = {row[0]: row[2] for row in detail_rows(no_limit)}
        self.assertEqual(values["system_estimate"], "充滿 1:36")
        stalled = laptop_from(sysfs.asus, SystemTimes(reports=True, full_stalled=True),
                              charge_control_end_threshold=100)
        values = {row[0]: row[2] for row in detail_rows(stalled)}
        self.assertEqual(values["system_estimate"], "—")
        plugged = laptop_from(sysfs.not_charging_at_limit, SystemTimes(reports=True))
        values = {row[0]: row[2] for row in detail_rows(plugged)}
        self.assertEqual(values["system_estimate"], "接電中")

    def test_snapshot_without_percent_still_renders(self):
        power = laptop_from(sysfs.garbage)
        self.assertEqual(dataclasses.replace(power).battery.percent, None)
        self.assertTrue(all(isinstance(row[2], str) for row in detail_rows(power)))


if __name__ == "__main__":
    unittest.main()
