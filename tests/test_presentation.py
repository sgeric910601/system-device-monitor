"""充電狀態與電力流向的顯示濾波：移植參考專案 ChargingPresentationTests 的數值。

macOS 的 BatterySnapshot 以 SMC 輸入與系統功率相減得到電池功率；這裡直接給電池功率
（battery_w），Registry 電壓 × 電流的情境同樣換算成瓦數（12000 mV × -1000 mA = -12 W）。
"""
import dataclasses
import unittest

from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              BatterySnapshot)
from sdm.core.presentation import (FLOW_ADAPTER, FLOW_BATTERY, FLOW_CHARGING, FLOW_MIXED,
                                   ChargingPresentation, PowerFlowPresentation, raw_flow_kind)


def snap(**fields):
    return BatterySnapshot(**fields)


class FullLatch(unittest.TestCase):
    def test_full_priority_and_brief_flag_changes(self):
        presentation = ChargingPresentation()
        s = snap(percent=100, ac_online=True, full_flag=True, battery_w=12 - 13.1)
        self.assertEqual(presentation.state(s, 0), FULL)
        for t in range(1, 13):
            s = dataclasses.replace(s, charging_flag=t % 2 == 0, full_flag=t % 3 == 0)
            self.assertEqual(presentation.state(s, t), FULL)

    def test_full_hysteresis_and_sustained_recharge(self):
        presentation = ChargingPresentation()
        s = snap(percent=100, ac_online=True, full_flag=True)
        self.assertEqual(presentation.state(s, 0), FULL)
        s = dataclasses.replace(s, percent=99, full_flag=False)
        self.assertEqual(presentation.state(s, 1), FULL)
        s = dataclasses.replace(s, charging_flag=True)
        self.assertEqual(presentation.state(s, 2), FULL)
        self.assertEqual(presentation.state(s, 11), FULL)
        self.assertEqual(presentation.state(s, 12), CHARGING)
        s = dataclasses.replace(s, charging_flag=False)
        self.assertEqual(presentation.state(s, 13), CHARGING)
        self.assertEqual(presentation.state(s, 23), FULL)
        self.assertEqual(presentation.state(dataclasses.replace(s, percent=98), 24), FULL)
        self.assertEqual(presentation.state(dataclasses.replace(s, percent=97), 25), PLUGGED_IDLE)

    def test_interrupted_confirmation_and_unplug_are_immediate(self):
        presentation = ChargingPresentation()
        s = snap(percent=100, ac_online=True)
        self.assertEqual(presentation.state(s, 0), FULL)
        s = dataclasses.replace(s, percent=99, charging_flag=True)
        self.assertEqual(presentation.state(s, 1), FULL)
        self.assertEqual(presentation.state(dataclasses.replace(s, charging_flag=False), 9), FULL)
        self.assertEqual(presentation.state(s, 10), FULL)
        self.assertEqual(presentation.state(s, 19), FULL)
        self.assertEqual(presentation.state(dataclasses.replace(s, ac_online=False), 19.1),
                         DISCHARGING)
        self.assertEqual(presentation.state(s, 20), CHARGING)

    def test_real_mixed_supply_at_full_keeps_the_full_state(self):
        presentation = ChargingPresentation()
        s = snap(percent=100, ac_online=True, full_flag=True, battery_w=0.0)
        self.assertEqual(presentation.state(s, 0), FULL)
        s = dataclasses.replace(s, battery_w=-12.0)
        self.assertEqual(presentation.state(s, 1), FULL)
        self.assertEqual(presentation.state(s, 11), FULL)

    def test_full_flag_cleared_at_100_percent_stays_full(self):
        presentation = ChargingPresentation()
        s = snap(percent=100, ac_online=True, full_flag=True, battery_w=-1.2)
        self.assertEqual(presentation.state(s, 0), FULL)
        self.assertEqual(presentation.state(dataclasses.replace(s, full_flag=False), 20), FULL)


class LimitChanges(unittest.TestCase):
    def test_charge_limit_and_mixed_supply_outside_full_are_preserved(self):
        presentation = ChargingPresentation()
        s = snap(percent=100, ac_online=True, full_flag=True)
        self.assertEqual(presentation.state(s, 0), FULL)
        s = dataclasses.replace(s, limit=80, battery_w=-12.0)
        self.assertEqual(presentation.state(s, 1), LIMIT_REACHED)
        s = dataclasses.replace(s, limit=None, full_flag=False, percent=75)
        self.assertEqual(presentation.state(s, 2), PLUGGED_IDLE)
        # 參考專案此時沒有 SMC 功率、改看晶片旗標；Linux 的方向來自狀態，充電中即為充入
        s = dataclasses.replace(s, charging_flag=True, battery_w=12.0)
        self.assertEqual(presentation.state(s, 3), PLUGGED_IDLE)
        self.assertEqual(presentation.state(s, 13), CHARGING)

    def test_charge_limit_flapping_is_confirmed(self):
        presentation = ChargingPresentation()
        flow = PowerFlowPresentation()
        s = snap(percent=94, ac_online=True, voltage_v=12.6, limit=95, battery_w=7 - 6.5)
        self.assertEqual(presentation.state(s, 0), LIMIT_REACHED)
        self.assertEqual(flow.kind(True, 0.5, True, 0), FLOW_ADAPTER)
        for t in range(1, 10):
            s = dataclasses.replace(s, battery_w=(7 if t % 3 == 0 else 20) - 6.5)
            self.assertEqual(presentation.state(s, t), LIMIT_REACHED)
        s = dataclasses.replace(s, battery_w=20 - 6.5)
        self.assertEqual(presentation.state(s, 10), LIMIT_REACHED)
        self.assertEqual(presentation.state(s, 20), CHARGING)
        self.assertEqual(presentation.state(dataclasses.replace(s, ac_online=False), 20.5),
                         DISCHARGING)

    # 未移植：testStaleRegistryFlagUsesLivePower 的「晶片旗標停在舊值」情境屬於 macOS；
    # 其中 chargingNow 的門檻數值已移植到 test_battery.ChargingRule。
    # 未移植：testIntelTrickleChargeFollowsBatteryChip — Intel Mac 的 SMC 涓流充電誤差，Linux 沒有。


class FlowConfirmation(unittest.TestCase):
    def test_initial_near_full_spike_also_needs_confirmation(self):
        flow = PowerFlowPresentation()
        self.assertEqual(flow.kind(True, 7, True, 0), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -5, True, 1), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, 0.2, True, 2), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -12, True, 3), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -12, True, 13), FLOW_MIXED)

    def test_real_mixed_supply_at_full_is_not_hidden(self):
        flow = PowerFlowPresentation()
        self.assertEqual(flow.kind(True, 0, True, 0), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -12, True, 1), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -12, True, 11), FLOW_MIXED)

    def test_minor_assist_and_flow_confirmation(self):
        flow = PowerFlowPresentation()
        self.assertEqual(flow.kind(True, 0, True, 0), FLOW_ADAPTER)
        for t in range(1, 21):
            self.assertEqual(flow.kind(True, 1.9 if t % 2 == 0 else -1.9, True, t), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -3, True, 21), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -3, True, 30), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -3, True, 31), FLOW_MIXED)
        self.assertEqual(flow.kind(True, 4, True, 32), FLOW_MIXED)
        self.assertEqual(flow.kind(True, 4, True, 42), FLOW_CHARGING)
        self.assertEqual(flow.kind(True, -1, True, 43), FLOW_CHARGING)
        self.assertEqual(flow.kind(True, -1, True, 53), FLOW_ADAPTER)
        self.assertEqual(flow.kind(False, -1, True, 53.1), FLOW_BATTERY)
        self.assertEqual(flow.kind(True, -1, True, 53.2), FLOW_ADAPTER)
        self.assertEqual(flow.kind(True, -1, False, 53.3), FLOW_MIXED)

    def test_plug_in_from_battery_assist_to_charging_switches_at_once(self):
        flow = PowerFlowPresentation()
        self.assertEqual(flow.kind(True, -13, False, 0), FLOW_MIXED)
        self.assertEqual(flow.kind(True, 14, True, 13), FLOW_CHARGING)
        held = PowerFlowPresentation()
        self.assertEqual(held.kind(True, -3, False, 0), FLOW_MIXED)
        self.assertEqual(held.kind(True, 4, True, 1), FLOW_MIXED)
        self.assertEqual(held.kind(True, 4, True, 11), FLOW_CHARGING)

    def test_raw_kind_thresholds(self):
        self.assertEqual(raw_flow_kind(False, 30, False), FLOW_BATTERY)
        self.assertEqual(raw_flow_kind(True, 1.9, True), FLOW_ADAPTER)
        self.assertEqual(raw_flow_kind(True, 0.06, False), FLOW_CHARGING)
        self.assertEqual(raw_flow_kind(True, -0.06, False), FLOW_MIXED)
        self.assertEqual(raw_flow_kind(True, 0.04, False), FLOW_ADAPTER)


if __name__ == "__main__":
    unittest.main()
