"""電池快照：多顆電池合併、充電判斷、系統耗電、健康度與狀態（純邏輯）。"""
import dataclasses
from pathlib import Path
import tempfile
import unittest

from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              BatterySnapshot, combine)
from sdm.data.power_supply import AdapterReading, BatteryReading, read_adapters, read_batteries
from tests.fixtures import sysfs


def from_tree(builder, on_battery=None, upower_state=None, **options):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        builder(root, **options)
        return combine(read_batteries(root), read_adapters(root), on_battery, upower_state)


def usb_adapter(volts, amps, online=True):
    return AdapterReading(name="ucsi-source-psy-USBC000:001", kind="USB", online=online,
                          voltage_v=volts, current_a=amps)


class Fixtures(unittest.TestCase):
    def test_thinkpad_on_battery(self):
        s = from_tree(sysfs.thinkpad)
        self.assertEqual((s.percent, s.ac_online, s.status, s.state), (82, False, "Discharging",
                                                                      DISCHARGING))
        self.assertEqual(s.battery_w, -15.0)
        # 用電池、變壓器未知：系統耗電就是電池放出的 15 W
        self.assertEqual(s.system_w, 15.0)
        self.assertAlmostEqual(s.current_a, -15 / 12.45)
        self.assertAlmostEqual(s.health_pct, 90.909, places=3)
        self.assertEqual((s.limit, s.limit_start, s.limit_supported), (80, 75, True))
        self.assertEqual((s.energy_now_wh, s.energy_full_wh, s.energy_design_wh), (41.0, 50.0, 55.0))
        self.assertAlmostEqual(s.charge_now_ah, 41 / 11.55)
        self.assertEqual((s.cycles, s.temperature_c, s.rate_unit), (212, 31.4, "W"))
        self.assertEqual((s.adapter_w, s.adapter_negotiated_w, s.count), (None, None, 1))

    def test_thinkpad_charging_with_unmeasured_mains_has_unknown_system_power(self):
        s = from_tree(sysfs.thinkpad, online=1, status="Charging", capacity=60,
                      energy_now=30_000_000, power_now=20_000_000)
        self.assertEqual((s.ac_online, s.battery_w, s.charging, s.state), (True, 20.0, True, CHARGING))
        self.assertIsNone(s.system_w)

    def test_asus_negative_current_while_charging_is_charging(self):
        s = from_tree(sysfs.asus)
        self.assertEqual((s.percent, s.ac_online, s.state, s.rate_unit), (64, True, CHARGING, "A"))
        self.assertAlmostEqual(s.current_a, 2.1)
        self.assertAlmostEqual(s.battery_w, 2.1 * 15.6)
        self.assertEqual((s.limit, s.limit_start, s.cycles), (80, None, None))
        self.assertAlmostEqual(s.health_pct, 87.5)
        self.assertAlmostEqual(s.energy_now_wh, 2.688 * 15.4)

    def test_framework_usb_pd_contract_is_not_a_measurement(self):
        s = from_tree(sysfs.framework)
        self.assertEqual((s.adapter_negotiated_w, s.adapter_rated_w, s.adapter_v, s.adapter_a),
                         (45.0, 65.0, 20.0, 2.25))
        # 協商功率只是上限：接電時整機耗電未知，不拿它來推算
        self.assertIsNone(s.adapter_w)
        self.assertAlmostEqual(s.battery_w, 25.2)
        self.assertIsNone(s.system_w)
        self.assertEqual((s.limit, s.state), (90, CHARGING))

    def test_not_charging_at_the_end_threshold(self):
        s = from_tree(sysfs.not_charging_at_limit)
        self.assertEqual((s.percent, s.battery_w, s.charging, s.state), (80, 0.0, False,
                                                                        LIMIT_REACHED))

    def test_unknown_status_on_mains_is_idle(self):
        s = from_tree(sysfs.unknown_status)
        self.assertEqual((s.percent, s.battery_w, s.state), (91, 0.0, PLUGGED_IDLE))
        self.assertFalse(s.limit_supported)

    def test_upower_state_breaks_the_tie_for_an_unknown_status(self):
        charging = from_tree(sysfs.unknown_status, upower_state=1, power_now=15_000_000)
        self.assertEqual((charging.battery_w, charging.charging, charging.state),
                         (15.0, True, CHARGING))
        draining = from_tree(sysfs.unknown_status, upower_state=2, power_now=15_000_000)
        self.assertEqual((draining.battery_w, draining.state), (-15.0, PLUGGED_IDLE))
        idle = from_tree(sysfs.unknown_status, upower_state=4, power_now=15_000_000)
        self.assertEqual((idle.battery_w, idle.state), (0.0, PLUGGED_IDLE))
        not_charging = from_tree(sysfs.not_charging_at_limit, upower_state=1, power_now=500_000)
        self.assertEqual(not_charging.battery_w, 0.5)

    def test_upower_state_never_overrides_a_definite_status(self):
        s = from_tree(sysfs.thinkpad, online=1, upower_state=1)
        self.assertEqual(s.battery_w, -15.0)

    def test_no_thresholds(self):
        s = from_tree(sysfs.no_thresholds)
        self.assertEqual((s.limit, s.limit_supported, s.percent), (None, False, 47))

    def test_end_threshold_of_100_is_no_limit(self):
        s = from_tree(sysfs.thinkpad, charge_control_end_threshold=100)
        self.assertEqual((s.limit, s.limit_supported), (None, True))

    def test_two_batteries_are_combined(self):
        s = from_tree(sysfs.two_batteries)
        self.assertEqual((s.count, s.percent, s.status), (2, 80, "Discharging"))
        self.assertEqual(s.battery_w, -8.0)
        self.assertEqual((s.energy_now_wh, s.energy_full_wh, s.energy_design_wh), (57.6, 72.0, 74.0))
        self.assertAlmostEqual(s.health_pct, 72 / 74 * 100)
        self.assertEqual((s.temperature_c, s.cycles, s.limit, s.limit_start), (32.0, 120, 85, 70))

    def test_peripheral_battery_alone_is_no_laptop(self):
        self.assertIsNone(from_tree(sysfs.peripheral))

    def test_garbage_values_do_not_break_the_snapshot(self):
        s = from_tree(sysfs.garbage)
        self.assertEqual((s.percent, s.battery_w, s.system_w, s.health_pct), (None, None, None, None))
        self.assertEqual((s.ac_online, s.state, s.limit), (False, DISCHARGING, None))


class Combination(unittest.TestCase):
    def test_percent_falls_back_to_capacity_average(self):
        batteries = (BatteryReading(name="BAT0", capacity=40), BatteryReading(name="BAT1", capacity=61))
        self.assertEqual(combine(batteries, ()).percent, 51)

    def test_single_battery_prefers_the_kernel_capacity(self):
        battery = BatteryReading(name="BAT0", capacity=79, energy_now_wh=40.0, energy_full_wh=50.0)
        self.assertEqual(combine((battery,), ()).percent, 79)

    def test_without_adapters_upower_decides_the_supply(self):
        battery = BatteryReading(name="BAT0", status="Unknown", capacity=50)
        self.assertTrue(combine((battery,), (), on_battery=False).ac_online)
        self.assertFalse(combine((battery,), (), on_battery=True).ac_online)
        self.assertTrue(combine((dataclasses.replace(battery, status="Full"),), ()).ac_online)
        self.assertFalse(combine((battery,), ()).ac_online)

    def test_charging_battery_means_external_power_even_with_an_offline_adapter(self):
        s = from_tree(sysfs.thinkpad, online=0, status="Charging", power_now=20_000_000)
        self.assertEqual((s.ac_online, s.battery_w, s.charging, s.state), (True, 20.0, True,
                                                                          CHARGING))

    def test_inconsistent_adapter_reading_gives_no_system_power(self):
        s = BatterySnapshot(percent=50, ac_online=True, battery_w=30.0, adapter_w=5.0)
        self.assertIsNone(s.system_w)

    def test_usb_pd_adapter_only_reports_the_contract(self):
        battery = BatteryReading(name="BAT0", status="Charging", capacity=50, power_w=25.0)
        s = combine((battery,), (usb_adapter(20.0, 2.0),))
        self.assertEqual((s.adapter_negotiated_w, s.adapter_w, s.system_w), (40.0, None, None))


class PowerSourceCases(unittest.TestCase):
    """參考 PowerSourceTests 的情境。Linux 目前沒有任何來源量得到變壓器的實際輸入
    （USB-C PD 只有協商值），所以量測值（adapter_w）直接放進快照，驗證保留下來的算法。"""

    def test_measured_adapter_minus_charge_is_system_power(self):
        s = BatterySnapshot(percent=80, ac_online=True, charging_flag=True, battery_w=25.0,
                            adapter_w=40.0)
        self.assertEqual((s.adapter_w, s.battery_w, s.system_w), (40.0, 25.0, 15.0))
        s = dataclasses.replace(s, battery_w=26.0, adapter_w=42.0)
        self.assertAlmostEqual(s.system_w, 16.0)

    def test_weak_adapter_drains_the_battery(self):
        battery = BatteryReading(name="BAT0", status="Discharging", capacity=40, power_w=10.0,
                                 voltage_v=12.0, charge_now_ah=2.0)
        s = combine((battery,), (usb_adapter(5.0, 1.0),))
        self.assertEqual((s.ac_online, s.battery_w, s.system_w), (True, -10.0, None))
        self.assertTrue(s.draining)
        self.assertEqual(s.state, PLUGGED_IDLE)
        measured = dataclasses.replace(s, adapter_w=5.0)
        self.assertEqual(measured.system_w, 15.0)
        self.assertAlmostEqual(measured.adapter_w - measured.system_w - measured.battery_w, 0.0)

    def test_mixed_supply_is_not_classified_as_full(self):
        s = BatterySnapshot(percent=100, ac_online=True, full_flag=True, battery_w=-12.0)
        self.assertEqual(s.state, PLUGGED_IDLE)

    def test_idle_full_battery(self):
        s = BatterySnapshot(percent=100, ac_online=True, full_flag=True)
        self.assertEqual(s.state, FULL)

    def test_draining_to_the_limit_stays_plugged(self):
        s = BatterySnapshot(percent=100, ac_online=True, battery_w=-3.3, limit=95)
        self.assertEqual(s.state, LIMIT_REACHED)
        self.assertTrue(s.draining_above_limit)

    # 未移植：testMissingSMCSensorUsesRegistryInsteadOfPPBR、testUnplugIgnoresDelayedPositivePowerReadings、
    # testUnplugThenReplugUsesLiveSupply、testFullBatteryWithStaleACFlagSwitchesToBattery、
    # testIdleFullAndMissingTelemetryKeepAC — 都是 macOS SMC／IORegistry 舊旗標的校正，Linux 沒有這些來源。
    # 未移植：testAdapterInputPairsWithLaggingSystemPower — SMC PDTR／PSTR 取樣時間配對，Linux 沒有。
    # 未移植：testDesktopModelNames — Mac 機型名稱判斷；Linux 改看有沒有系統電池（見 test_laptop_service）。


class ChargingRule(unittest.TestCase):
    """參考 chargingNow：≥3 W 算充電、<0.5 W 不算，中間看核心旗標；達上限時兩者都要成立。"""

    def snapshot(self, watts, flag, percent=90):
        return BatterySnapshot(percent=percent, ac_online=True, charging_flag=flag, battery_w=watts,
                               limit=95, voltage_v=12.6)

    def test_thresholds_from_the_reference(self):
        self.assertTrue(self.snapshot(20.7 - 6.3, False).charging)
        self.assertEqual(self.snapshot(20.7 - 6.3, False).state, CHARGING)
        self.assertFalse(self.snapshot(6.5 - 6.3, True).charging)
        self.assertEqual(self.snapshot(6.5 - 6.3, True).state, PLUGGED_IDLE)
        self.assertTrue(self.snapshot(8.0 - 6.3, True).charging)
        self.assertFalse(self.snapshot(8.0 - 6.3, False).charging)
        at_limit = self.snapshot(10.8 - 6.3, False, percent=95)
        self.assertFalse(at_limit.charging)
        self.assertEqual(at_limit.state, LIMIT_REACHED)
        self.assertTrue(self.snapshot(10.8 - 6.3, True, percent=95).charging)

    def test_unknown_power_follows_the_flag(self):
        self.assertTrue(BatterySnapshot(percent=50, ac_online=True, charging_flag=True).charging)
        self.assertFalse(BatterySnapshot(percent=50, charging_flag=True).charging)

    def test_draining_above_limit_needs_a_real_discharge(self):
        s = BatterySnapshot(percent=97, ac_online=True, battery_w=4 - 6.5, limit=95)
        self.assertEqual(s.state, LIMIT_REACHED)
        self.assertTrue(s.draining_above_limit)
        self.assertFalse(dataclasses.replace(s, battery_w=6.2 - 6.5).draining_above_limit)
        self.assertFalse(dataclasses.replace(s, percent=95).draining_above_limit)

    def test_current_decides_draining_without_power(self):
        self.assertTrue(BatterySnapshot(ac_online=True, current_a=-0.2).draining)
        self.assertFalse(BatterySnapshot(ac_online=True, current_a=-0.02).draining)

    def test_snapshot_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            BatterySnapshot().percent = 5


if __name__ == "__main__":
    unittest.main()
