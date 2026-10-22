"""剩餘時間、充電時間與近 3 分鐘平均：移植參考專案 Estimator 與 PowerSourceTests 的數值。

系統預估來自 UPower（TimeToEmpty／TimeToFull，0 代表還在估算），對應 macOS 的電源資訊。
"""
import dataclasses
import math
import unittest

from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              UNIT_AMPS, UNIT_WATTS, BatterySnapshot)
from sdm.core.estimates import (Averages, LiveAverages, SystemTimes, SystemWatch, estimate,
                                minutes_to_charge)
from sdm.constants import Battery
from sdm.core.presentation import FLOW_ADAPTER, FLOW_BATTERY, FLOW_CHARGING, FLOW_MIXED

UPOWER_ESTIMATING = SystemTimes(reports=True)


def amps(discharge=None, charge=None):
    return Averages(discharge=discharge, charge=charge, unit=UNIT_AMPS)


class ReferenceStates(unittest.TestCase):
    def test_full_with_small_discharge_is_drawn_from_the_adapter(self):
        s = BatterySnapshot(percent=100, ac_online=True, full_flag=True, battery_w=12 - 13.1)
        e = estimate(s, display_state=FULL)
        self.assertEqual(e.state, FULL)
        self.assertAlmostEqual(e.battery_w, -1.1)
        self.assertEqual(e.flow_kind, FLOW_ADAPTER)
        self.assertIsNone(e.to_full)

    def test_unplugged_ignores_the_display_state(self):
        e = estimate(BatterySnapshot(percent=99, charging_flag=True), display_state=FULL)
        self.assertEqual((e.state, e.flow_kind), (DISCHARGING, FLOW_BATTERY))

    def test_confirmed_mixed_flow_at_full(self):
        s = BatterySnapshot(percent=100, ac_online=True, full_flag=True, battery_w=-12.0)
        e = dataclasses.replace(estimate(s, display_state=FULL), display_flow=FLOW_MIXED)
        self.assertEqual((e.state, e.flow_kind, e.battery_w), (FULL, FLOW_MIXED, -12.0))

    def test_draining_while_plugged_is_drawn_as_mixed(self):
        s = BatterySnapshot(percent=75, ac_online=True, battery_w=-12.0)
        self.assertEqual(estimate(s).flow_kind, FLOW_MIXED)

    def test_charging_towards_the_limit(self):
        s = BatterySnapshot(percent=90, ac_online=True, battery_w=20.7 - 6.3, limit=95,
                            charge_full_ah=4.7, voltage_v=12.6)
        e = estimate(s, amps(charge=1.1))
        self.assertEqual((e.state, e.flow_kind), (CHARGING, FLOW_CHARGING))
        self.assertEqual(e.to_limit, 13)
        self.assertEqual(e.headline_to_full, 13)


class SystemEstimate(unittest.TestCase):
    """參考 testSystemTimeWaitsForMacOSEstimate：系統還在估算時不改用其他算法。"""

    def setUp(self):
        self.discharging = BatterySnapshot(percent=49, voltage_v=7.58, current_a=-2.4,
                                           battery_w=-2.4 * 7.58, charge_now_ah=3.0,
                                           rate_unit=UNIT_AMPS)
        self.charging = BatterySnapshot(percent=70, ac_online=True, charging_flag=True,
                                        voltage_v=8.2, current_a=3.1, battery_w=3.1 * 8.2,
                                        charge_full_ah=6.031, charge_now_ah=4.222,
                                        rate_unit=UNIT_AMPS)

    def test_time_to_empty_waits_for_upower(self):
        e = estimate(self.discharging, amps(discharge=2.0), system=UPOWER_ESTIMATING)
        self.assertIsNone(e.system_to_empty)
        self.assertEqual(e.live_to_empty, 90)
        self.assertEqual(e.headline_to_empty, 90)
        e = estimate(self.discharging, amps(discharge=2.0),
                     system=SystemTimes(reports=True, to_empty_min=95))
        self.assertEqual((e.system_to_empty, e.headline_to_empty), (95, 95))
        self.assertIsNone(estimate(self.discharging, amps(discharge=2.0)).system_to_empty)

    def test_nothing_known_yet(self):
        e = estimate(self.discharging, system=UPOWER_ESTIMATING)
        self.assertEqual((e.system_to_empty, e.live_to_empty, e.headline_to_empty),
                         (None, None, None))

    def test_time_to_full_never_falls_back_to_a_straight_line_while_upower_estimates(self):
        e = estimate(self.charging, amps(charge=3.166), display_state=CHARGING,
                     system=UPOWER_ESTIMATING)
        self.assertIsNone(e.to_full)
        e = estimate(self.charging, amps(charge=3.166), display_state=CHARGING,
                     system=SystemTimes(reports=True, to_full_min=80))
        self.assertEqual(e.to_full, 80)

    def test_upower_that_never_estimates_falls_back_to_an_approximate_time(self):
        # 刻意與參考不同：Linux 許多驅動的 TimeToFull 一直是 0，等滿 2 分鐘才改用平均推算並標示「約」
        waiting = estimate(self.charging, amps(charge=3.166), display_state=CHARGING,
                           system=SystemTimes(reports=True, full_stalled=False))
        self.assertEqual((waiting.to_full, waiting.to_full_approx), (None, False))
        stalled = estimate(self.charging, amps(charge=3.166), display_state=CHARGING,
                           system=SystemTimes(reports=True, full_stalled=True))
        self.assertEqual((stalled.to_full, stalled.to_full_approx), (34, True))
        no_average = estimate(self.charging, display_state=CHARGING,
                              system=SystemTimes(reports=True, full_stalled=True))
        self.assertEqual((no_average.to_full, no_average.to_full_approx), (None, False))
        upower = estimate(self.charging, amps(charge=3.166), display_state=CHARGING,
                          system=SystemTimes(reports=True, to_full_min=80, full_stalled=True))
        self.assertEqual((upower.to_full, upower.to_full_approx), (80, False))

    def test_stalled_time_to_empty_is_marked(self):
        e = estimate(self.discharging, amps(discharge=2.0),
                     system=SystemTimes(reports=True, empty_stalled=True))
        self.assertEqual((e.system_to_empty, e.system_stalled, e.headline_to_empty), (None, True, 90))

    def test_minutes_to_charge_without_a_system_estimate(self):
        e = estimate(self.charging, amps(charge=3.166), display_state=CHARGING)
        self.assertEqual(e.to_full, 34)

    def test_reference_preview_numbers(self):
        s = BatterySnapshot(percent=78, voltage_v=12.45, current_a=-0.52,
                            battery_w=-0.52 * 12.45, charge_now_ah=3.9, rate_unit=UNIT_AMPS)
        e = estimate(s, amps(discharge=0.52), system=SystemTimes(reports=True, to_empty_min=445))
        self.assertEqual((e.system_to_empty, e.system_to_10), (445, 387))
        self.assertEqual((e.live_to_empty, e.live_to_10), (450, 392))

    def test_below_the_reserve_gives_zero(self):
        s = dataclasses.replace(self.discharging, percent=8)
        e = estimate(s, amps(discharge=2.0), system=SystemTimes(reports=True, to_empty_min=20))
        self.assertEqual((e.system_to_10, e.live_to_10), (0, 0))

    def test_live_estimate_needs_a_real_current(self):
        self.assertIsNone(estimate(self.discharging, amps(discharge=0.010)).live_to_empty)
        self.assertIsNotNone(estimate(self.discharging, amps(discharge=0.011)).live_to_empty)
        watts = BatterySnapshot(percent=50, battery_w=-0.1, energy_now_wh=20.0,
                                rate_unit=UNIT_WATTS)
        self.assertIsNone(estimate(watts, Averages(discharge=0.1, unit=UNIT_WATTS)).live_to_empty)
        self.assertEqual(estimate(watts, Averages(discharge=10.0, unit=UNIT_WATTS)).live_to_empty,
                         120)


class MinutesToCharge(unittest.TestCase):
    def test_from_the_charge_average_or_the_time_to_full(self):
        s = BatterySnapshot(percent=70, ac_online=True, charging_flag=True, battery_w=20.0,
                            charge_full_ah=6.0)
        e = estimate(s, amps(charge=3.0))
        self.assertEqual(minutes_to_charge(80, s, e), 12)
        e = dataclasses.replace(e, averages=Averages(), to_full=90)
        self.assertEqual(minutes_to_charge(80, s, e), 30)
        self.assertIsNone(minutes_to_charge(70, s, e))
        self.assertIsNone(minutes_to_charge(80, s, dataclasses.replace(e, state=PLUGGED_IDLE)))


class PowerSourceCases(unittest.TestCase):
    """參考 PowerSourceTests：系統耗電與電池功率互相平衡。"""

    def test_successive_samples_refresh_every_branch(self):
        s = BatterySnapshot(percent=80, ac_online=True, charging_flag=True, battery_w=25.0,
                            adapter_w=40.0)
        e = estimate(s)
        self.assertEqual((e.adapter_w, e.battery_w, e.system_w), (40.0, 25.0, 15.0))
        e = estimate(dataclasses.replace(s, battery_w=26.0, adapter_w=42.0))
        self.assertEqual((e.adapter_w, e.battery_w, e.system_w), (42.0, 26.0, 16.0))

    def test_replug_at_full(self):
        s = BatterySnapshot(percent=100, ac_online=True, full_flag=True, battery_w=1.0,
                            adapter_w=18.0)
        e = estimate(s)
        self.assertEqual((e.state, e.battery_w, e.adapter_w, e.system_w), (FULL, 1.0, 18.0, 17.0))

    def test_on_battery_system_power_is_the_discharge(self):
        s = BatterySnapshot(percent=100, battery_w=-12.682 * 1.021, current_a=-1.021,
                            voltage_v=12.682, charge_now_ah=5.275, rate_unit=UNIT_AMPS)
        e = estimate(s, amps(discharge=1.021))
        self.assertEqual(e.state, DISCHARGING)
        self.assertAlmostEqual(e.system_w, -e.battery_w)
        self.assertIsNotNone(e.live_to_empty)
        self.assertIsNone(e.adapter_w)
        e = estimate(BatterySnapshot(percent=88, battery_w=-3.2))
        self.assertEqual((e.battery_w, e.system_w), (-3.2, 3.2))

    def test_battery_power_balances_input_and_system(self):
        s = BatterySnapshot(percent=88, ac_online=True, charging_flag=True, battery_w=13.46,
                            adapter_w=20.44)
        e = estimate(s, amps(charge=1.069))
        self.assertEqual(e.state, CHARGING)
        self.assertAlmostEqual(e.adapter_w - e.system_w - e.battery_w, 0.0)

    def test_draining_to_the_charge_limit_stays_plugged(self):
        s = BatterySnapshot(percent=100, ac_online=True, battery_w=-3.3, limit=95)
        e = estimate(s, amps(discharge=0.264))
        self.assertEqual(e.state, LIMIT_REACHED)
        self.assertAlmostEqual(e.battery_w, -3.3)

    def test_weak_adapter_draining_shows_remaining_time(self):
        s = BatterySnapshot(percent=40, ac_online=True, voltage_v=12.0, current_a=-10 / 12,
                            battery_w=-10.0, adapter_w=5.0, charge_now_ah=2.0, rate_unit=UNIT_AMPS)
        e = estimate(s, amps(discharge=0.833))
        self.assertEqual(e.state, PLUGGED_IDLE)
        self.assertTrue(e.draining_on_ac)
        self.assertEqual(e.battery_w, -10.0)
        self.assertEqual(e.live_to_empty, 144)
        self.assertEqual(e.headline_to_empty, 144)
        self.assertAlmostEqual(e.adapter_w - e.system_w - e.battery_w, 0.0)
        self.assertEqual(e.flow_kind, FLOW_MIXED)

    def test_just_plugged_in_is_not_a_weak_adapter(self):
        s = BatterySnapshot(percent=40, ac_online=True, battery_w=-10.0)
        self.assertFalse(estimate(s, just_plugged=True).draining_on_ac)

    def test_health_is_full_over_design(self):
        e = estimate(BatterySnapshot(energy_full_wh=50.0, energy_design_wh=55.0))
        self.assertAlmostEqual(e.health_pct, 90.909, places=3)


class Watch(unittest.TestCase):
    """UPower 回報 0 的時間：充電時看充滿、用電池時看剩餘；狀態改變或有值就重新計時。"""

    def test_full_window(self):
        watch, waiting = SystemWatch(), SystemTimes(reports=True)
        stall = Battery.UPOWER_STALL_SECONDS
        self.assertFalse(watch.update(waiting, CHARGING, False, 100.0).full_stalled)
        self.assertFalse(watch.update(waiting, CHARGING, False, 100.0 + stall - 1).full_stalled)
        self.assertTrue(watch.update(waiting, CHARGING, False, 100.0 + stall).full_stalled)
        given = SystemTimes(reports=True, to_full_min=40)
        self.assertFalse(watch.update(given, CHARGING, False, 100.0 + stall + 5).full_stalled)
        self.assertFalse(watch.update(waiting, CHARGING, False, 100.0 + stall + 10).full_stalled)

    def test_empty_window_and_state_changes(self):
        watch, waiting = SystemWatch(), SystemTimes(reports=True)
        stall = Battery.UPOWER_STALL_SECONDS
        watch.update(waiting, DISCHARGING, False, 0.0)
        self.assertTrue(watch.update(waiting, DISCHARGING, False, stall).empty_stalled)
        self.assertFalse(watch.update(waiting, CHARGING, False, stall + 1).empty_stalled)
        self.assertFalse(watch.update(waiting, PLUGGED_IDLE, True, stall + 2).empty_stalled)
        self.assertTrue(watch.update(waiting, PLUGGED_IDLE, True, 2 * stall + 2).empty_stalled)
        self.assertEqual(watch.update(SystemTimes(), DISCHARGING, False, 9e9), SystemTimes())


class Averaging(unittest.TestCase):
    """參考 BatteryStore.refresh：充放電分開平均、狀態改變就重算、剛開始充電的 60 秒不算。"""

    def test_first_value_then_three_minute_average(self):
        live = LiveAverages()
        s = BatterySnapshot(percent=50, battery_w=-10.0, energy_now_wh=20.0, rate_unit=UNIT_WATTS)
        self.assertEqual(live.update(DISCHARGING, s, 0.0), Averages(10.0, None, UNIT_WATTS))
        s = dataclasses.replace(s, battery_w=-20.0)
        expected = 10 + (1 - math.exp(-1)) * 10
        self.assertAlmostEqual(live.update(DISCHARGING, s, 180.0).discharge, expected)

    def test_state_change_resets_both_averages(self):
        live = LiveAverages()
        s = BatterySnapshot(percent=50, current_a=-1.0, charge_now_ah=2.0, rate_unit=UNIT_AMPS)
        live.update(DISCHARGING, s, 0.0)
        result = live.update(PLUGGED_IDLE, dataclasses.replace(s, current_a=0.0), 5.0)
        self.assertEqual(result, Averages(None, None, UNIT_AMPS))

    def test_charging_started_during_the_run_skips_the_first_minute(self):
        live = LiveAverages()
        s = BatterySnapshot(percent=50, current_a=-1.0, charge_now_ah=2.0, rate_unit=UNIT_AMPS)
        live.update(DISCHARGING, s, 0.0)
        charging = dataclasses.replace(s, current_a=2.0)
        self.assertIsNone(live.update(CHARGING, charging, 10.0).charge)
        self.assertIsNone(live.update(CHARGING, charging, 69.0).charge)
        self.assertEqual(live.update(CHARGING, charging, 70.0).charge, 2.0)

    def test_app_started_mid_charge_counts_at_once(self):
        live = LiveAverages()
        s = BatterySnapshot(percent=50, current_a=2.0, charge_now_ah=2.0, rate_unit=UNIT_AMPS)
        self.assertEqual(live.update(CHARGING, s, 0.0).charge, 2.0)

    def test_unit_change_or_unknown_rate(self):
        live = LiveAverages()
        s = BatterySnapshot(percent=50, battery_w=-10.0, current_a=-1.0, energy_now_wh=20.0,
                            rate_unit=UNIT_WATTS)
        live.update(DISCHARGING, s, 0.0)
        result = live.update(DISCHARGING, dataclasses.replace(s, rate_unit=UNIT_AMPS), 5.0)
        self.assertEqual(result, Averages(1.0, None, UNIT_AMPS))
        result = live.update(DISCHARGING, dataclasses.replace(s, rate_unit=None), 10.0)
        self.assertEqual(result, Averages(None, None, None))


if __name__ == "__main__":
    unittest.main()
