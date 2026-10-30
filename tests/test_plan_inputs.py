"""智慧充電的即時輸入（sdm.services.plan_inputs）：由筆電耗電快照算出判斷用的狀態、
充電速度與建立排程前的預估（參考專案 BatteryStore.chargingPlanInput／chargingForecast）。"""
from types import SimpleNamespace
import unittest

from sdm.constants import SmartCharging
from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              BatterySnapshot)
from sdm.core.estimates import Averages, Estimates
from sdm.services import plan_inputs

NOW = 1_800_000_000.0


def power(percent=62, plugged=True, state=CHARGING, charge=None, unit="W", full_wh=58.0,
          to_full=None):
    known = percent is not None and full_wh is not None
    battery = BatterySnapshot(percent=percent, ac_online=plugged, energy_full_wh=full_wh,
                              energy_now_wh=full_wh * percent / 100 if known else None,
                              charging_flag=state == CHARGING)
    estimates = Estimates(state=state, averages=Averages(charge=charge, unit=unit),
                          to_full=to_full)
    return SimpleNamespace(battery=battery, estimates=estimates, stamp=NOW)


class Inputs(unittest.TestCase):
    def test_charging_snapshot(self):
        data = plan_inputs.plan_input(power(charge=11.6), 100, True, 100, None, NOW)
        self.assertEqual((data.percent, data.connected, data.limit_supported, data.current_limit,
                          data.charging, data.paused), (62, True, True, 100, True, False))
        # 平均充電 11.6 W、滿充 58 Wh：每小時 20%，62→100 需要 114 分鐘
        self.assertEqual(data.estimated_minutes, 114)

    def test_holding_states_are_paused(self):
        for state in (PLUGGED_IDLE, LIMIT_REACHED, FULL):
            with self.subTest(state=state):
                data = plan_inputs.plan_input(power(80, state=state), 80, True, 100, None, NOW)
                self.assertEqual((data.charging, data.paused), (False, True))
        data = plan_inputs.plan_input(power(70, plugged=False, state=DISCHARGING), 80, True, 100,
                                      None, NOW)
        self.assertEqual((data.connected, data.charging, data.paused), (False, False, False))

    def test_history_rate_fills_in_without_a_live_estimate(self):
        data = plan_inputs.plan_input(power(70, state=PLUGGED_IDLE), 80, True, 100, 30.0, NOW)
        self.assertEqual(data.estimated_minutes, 60)
        data = plan_inputs.plan_input(power(70, state=PLUGGED_IDLE), 80, True, 100, None, NOW)
        self.assertIsNone(data.estimated_minutes)

    def test_no_reading_means_no_input(self):
        self.assertIsNone(plan_inputs.plan_input(None, 80, True, 100, None, NOW))
        self.assertIsNone(plan_inputs.plan_input(power(None), 80, True, 100, None, NOW))


class Rates(unittest.TestCase):
    def test_live_rate_from_watts_or_amps(self):
        self.assertAlmostEqual(plan_inputs.live_rate(power(charge=11.6)), 20.0)
        amps = power(charge=0.8, unit="A")
        amps = SimpleNamespace(battery=BatterySnapshot(percent=50, ac_online=True,
                                                       charge_full_ah=4.0),
                               estimates=amps.estimates, stamp=NOW)
        self.assertAlmostEqual(plan_inputs.live_rate(amps), 20.0)

    def test_no_live_rate_when_not_charging_or_too_small(self):
        self.assertIsNone(plan_inputs.live_rate(power(state=PLUGGED_IDLE, charge=11.6)))
        self.assertIsNone(plan_inputs.live_rate(power(charge=0.05)))
        self.assertIsNone(plan_inputs.live_rate(power(charge=11.6, full_wh=None)))

    def test_forecast_uses_live_then_history_then_the_fallback(self):
        self.assertAlmostEqual(plan_inputs.forecast_rate(power(charge=11.6), 30.0), 20.0)
        self.assertEqual(plan_inputs.forecast_rate(power(state=PLUGGED_IDLE), 30.0), 30.0)
        self.assertEqual(plan_inputs.forecast_rate(power(state=PLUGGED_IDLE), None),
                         SmartCharging.FALLBACK_RATE)

    def test_forecast_until_a_deadline(self):
        forecast = plan_inputs.make_forecast(power(65, state=PLUGGED_IDLE), 100, NOW + 3600, NOW,
                                             30.0)
        self.assertEqual((forecast.needed_minutes, forecast.reachable_percent), (102, 88))
        self.assertIsNone(plan_inputs.make_forecast(None, 100, NOW + 3600, NOW, 30.0))


if __name__ == "__main__":
    unittest.main()
