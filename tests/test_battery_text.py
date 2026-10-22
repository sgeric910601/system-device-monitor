"""筆電模式的電源頁與頂端列文字：不需要顯示器即可測試。"""
import dataclasses
import unittest

from sdm.core.battery import (CHARGING, FULL, LIMIT_REACHED, UNIT_AMPS, UNIT_WATTS,
                              BatterySnapshot)
from sdm.core.estimates import Averages, SystemTimes, estimate
from sdm.core.power_breakdown import Breakdown
from sdm.core.recap import DailyRecap
from sdm.services.battery_service import LaptopPower
from sdm.ui.battery_text import (TONE_CRITICAL, TONE_NORMAL, TONE_WARNING, flow_model, header,
                                 menu_line, minutes_phrase, recap_text, time_card, tray_icon,
                                 tray_label)

NOW = 1_800_000_000.0
UPOWER = SystemTimes(reports=True)
ON_BATTERY = BatterySnapshot(percent=82, battery_w=-6.5, current_a=-0.52, voltage_v=12.5,
                             charge_now_ah=3.9, rate_unit=UNIT_AMPS)
CHARGING_TO_LIMIT = BatterySnapshot(percent=62, ac_online=True, charging_flag=True, battery_w=18.0,
                                    energy_now_wh=36.0, energy_full_wh=58.0, rate_unit=UNIT_WATTS,
                                    limit=80, limit_start=75, limit_supported=True,
                                    adapter_negotiated_w=25.0, adapter_rated_w=65.0)


def laptop(snapshot, averages=Averages(), system=SystemTimes(), state=None, **fields):
    estimates = estimate(snapshot, averages, state, system, fields.pop("just_plugged", False))
    flow = fields.pop("flow", None)
    if flow is not None:
        estimates = dataclasses.replace(estimates, display_flow=flow)
    return LaptopPower(stamp=NOW, battery=snapshot, estimates=estimates, breakdown=Breakdown(),
                       **fields)


class Phrases(unittest.TestCase):
    def test_minutes_phrase(self):
        self.assertEqual([minutes_phrase(m) for m in (0, 36, 60, 205, 125)],
                         ["0 分鐘", "36 分鐘", "1 小時", "3 小時 25 分", "2 小時 5 分"])


class Header(unittest.TestCase):
    def test_on_battery_with_a_system_estimate(self):
        power = laptop(ON_BATTERY, system=SystemTimes(reports=True, to_empty_min=205))
        self.assertEqual(header(power), ("82%", "放電中", "還能用 3 小時 25 分", TONE_NORMAL))

    def test_still_estimating(self):
        self.assertEqual(header(laptop(ON_BATTERY, system=UPOWER))[2], "計算中…")

    def test_live_estimate_when_the_system_is_still_estimating(self):
        power = laptop(ON_BATTERY, Averages(discharge=0.52, unit=UNIT_AMPS), UPOWER)
        self.assertEqual(header(power)[2], "還能用 7 小時 30 分")

    def test_charging_to_full_and_to_the_limit(self):
        to_full = dataclasses.replace(CHARGING_TO_LIMIT, limit=None)
        power = laptop(to_full, system=SystemTimes(reports=True, to_full_min=36))
        self.assertEqual(header(power)[1:3], ("充電中", "36 分鐘後充滿"))
        power = laptop(CHARGING_TO_LIMIT, Averages(charge=18.0, unit=UNIT_WATTS), UPOWER)
        self.assertEqual(power.estimates.to_limit, 35)
        self.assertEqual(header(power)[1:3], ("充電中", "35 分鐘後到 80%"))
        self.assertEqual(header(laptop(CHARGING_TO_LIMIT, system=UPOWER))[2], "計算中…")

    def test_plugged_states(self):
        at_limit = BatterySnapshot(percent=80, ac_online=True, battery_w=0.0, limit=80)
        self.assertEqual(header(laptop(at_limit))[1:3], ("已達上限 80%", "暫停充電"))
        full = BatterySnapshot(percent=100, ac_online=True, full_flag=True, battery_w=0.0)
        self.assertEqual(header(laptop(full))[1:3], ("已充飽", "由變壓器供電"))
        idle = BatterySnapshot(percent=75, ac_online=True, battery_w=0.0)
        self.assertEqual(header(laptop(idle))[1:3], ("接電・未充電", "暫停充電"))
        draining = BatterySnapshot(percent=97, ac_online=True, battery_w=-2.5, limit=95)
        self.assertEqual(header(laptop(draining))[2], "放電到上限")

    def test_weak_adapter_is_a_warning_with_remaining_time(self):
        weak = BatterySnapshot(percent=40, ac_online=True, battery_w=-10.0, current_a=-0.833,
                               adapter_negotiated_w=5.0, charge_now_ah=2.0, rate_unit=UNIT_AMPS)
        power = laptop(weak, Averages(discharge=0.833, unit=UNIT_AMPS))
        self.assertEqual(header(power), ("40%", "接電・未充電", "供電不足・還能用 2 小時 24 分",
                                         TONE_WARNING))

    def test_low_battery_is_critical_and_unknown_percent(self):
        low = dataclasses.replace(ON_BATTERY, percent=9)
        self.assertEqual(header(laptop(low))[3], TONE_CRITICAL)
        self.assertEqual(header(laptop(dataclasses.replace(ON_BATTERY, percent=None)))[0], "—")


class Time(unittest.TestCase):
    def test_discharging_table(self):
        power = laptop(ON_BATTERY, Averages(discharge=0.52, unit=UNIT_AMPS), UPOWER,
                       unplugged_at=NOW - (2 * 3600 + 15 * 60))
        card = time_card(power, NOW)
        self.assertEqual(card.title, "預估可用時間")
        self.assertEqual(card.columns, ("", "系統預估", "依近 3 分鐘平均"))
        self.assertEqual(card.table, (("到 0%", "計算中…", "7 小時 30 分"),
                                      ("到 10%", "計算中…", "6 小時 35 分")))
        self.assertEqual(card.rows, (("已使用電池", "2 小時 15 分"),))
        self.assertEqual(card.note, "近 3 分鐘平均放電 520 mA（約 6.5 W）")

    def test_without_upower_and_with_history_unplug_time(self):
        power = laptop(ON_BATTERY)
        card = time_card(power, NOW, NOW - 600)
        self.assertEqual(card.table, (("到 0%", "—", "—"), ("到 10%", "—", "—")))
        self.assertEqual(card.rows, (("已使用電池", "10 分"),))
        self.assertEqual(time_card(power, NOW).rows, (("已使用電池", "—"),))

    def test_below_ten_percent(self):
        low = dataclasses.replace(ON_BATTERY, percent=8)
        power = laptop(low, Averages(discharge=0.52, unit=UNIT_AMPS),
                       SystemTimes(reports=True, to_empty_min=30))
        self.assertEqual(time_card(power, NOW).table[1], ("到 10%", "已低於 10%", "已低於 10%"))

    def test_watts_average_and_limit_row(self):
        watts = BatterySnapshot(percent=50, battery_w=-9.0, energy_now_wh=25.0,
                                rate_unit=UNIT_WATTS, limit=80, limit_start=75,
                                limit_supported=True)
        card = time_card(laptop(watts, Averages(discharge=9.0, unit=UNIT_WATTS)), NOW)
        self.assertEqual(card.note, "近 3 分鐘平均放電 9.0 W")
        self.assertEqual(card.rows[-1], ("充電上限", "80%（75% 以下才重新充電）"))

    def test_charging_rows(self):
        power = laptop(CHARGING_TO_LIMIT, Averages(charge=18.0, unit=UNIT_WATTS), UPOWER)
        card = time_card(power, NOW)
        self.assertEqual(card.title, "充電預估")
        self.assertEqual(card.table, ())
        self.assertEqual(card.rows, (("充到上限（80%）", "35 分"), ("近 3 分鐘平均充電", "18.0 W"),
                                     ("充入功率", "18.0 W")))
        no_limit = laptop(dataclasses.replace(CHARGING_TO_LIMIT, limit=None), system=UPOWER)
        self.assertEqual(time_card(no_limit, NOW).rows[:2],
                         (("充滿（100%）", "計算中…"), ("近 3 分鐘平均充電", "計算中…")))

    def test_plugged_messages(self):
        at_limit = BatterySnapshot(percent=80, ac_online=True, battery_w=0.0, limit=80,
                                   limit_supported=True, adapter_rated_w=65.0)
        card = time_card(laptop(at_limit), NOW)
        self.assertEqual((card.title, card.message), ("電源狀態", "已充到上限，暫停充電，由變壓器供電。"))
        self.assertEqual(card.rows, (("充電上限", "80%"), ("變壓器", "額定 65 W")))
        above = dataclasses.replace(at_limit, percent=83)
        self.assertEqual(time_card(laptop(above), NOW).message,
                         "已超過充電上限，暫停充電；降到 80% 以下才會再充。")
        draining = BatterySnapshot(percent=90, ac_online=True, battery_w=-6.0, limit=80,
                                   energy_full_wh=60.0, rate_unit=UNIT_WATTS)
        card = time_card(laptop(draining, Averages(discharge=6.0, unit=UNIT_WATTS)), NOW)
        self.assertEqual(card.message, "電量高於上限，系統正在讓電池慢慢降到 80%。")
        self.assertEqual(card.rows[-1], ("降到上限約需", "1 小時"))
        full = BatterySnapshot(percent=100, ac_online=True, full_flag=True, battery_w=0.0)
        self.assertEqual(time_card(laptop(full), NOW).message, "已充飽，由變壓器供電。")

    def test_plugged_idle_messages(self):
        weak = BatterySnapshot(percent=40, ac_online=True, battery_w=-10.0, current_a=-0.833,
                               adapter_negotiated_w=5.0, charge_now_ah=2.0, rate_unit=UNIT_AMPS)
        power = laptop(weak, Averages(discharge=0.833, unit=UNIT_AMPS))
        self.assertEqual(time_card(power, NOW).message,
                         "供電不足，電池仍在放電，預估剩餘 2 小時 24 分。")
        fresh = laptop(weak, just_plugged=True, plugged_in_at=NOW - 5)
        self.assertEqual(time_card(fresh, NOW).message, "剛接上電源，幾秒後就會開始充電。")
        idle = BatterySnapshot(percent=75, ac_online=True, battery_w=0.0)
        self.assertEqual(time_card(laptop(idle), NOW).message,
                         "接著電源但沒有在充電，可能是電池保護、溫度保護或充電門檻。")


class Flow(unittest.TestCase):
    def test_on_battery(self):
        model = flow_model(laptop(ON_BATTERY))
        self.assertEqual((model.adapter_text, model.adapter_active), ("未連接", False))
        self.assertEqual(model.system_text, "6.5 W")
        self.assertEqual((model.battery_title, model.battery_text, model.battery_active,
                          model.battery_charging), ("電池放電", "6.5 W", True, False))
        self.assertEqual(model.battery_icon, "battery-level-80-symbolic")

    def test_charging_with_a_negotiated_usb_pd_contract(self):
        model = flow_model(laptop(CHARGING_TO_LIMIT))
        self.assertEqual((model.adapter_text, model.adapter_active), ("協商 25 W", True))
        self.assertEqual((model.system_text, model.battery_title, model.battery_text),
                         ("—", "充入電池", "18.0 W"))
        self.assertTrue(model.battery_charging)
        self.assertEqual(model.battery_icon, "battery-level-60-charging-symbolic")

    def test_adapter_without_a_contract_shows_a_dash(self):
        rated = dataclasses.replace(CHARGING_TO_LIMIT, adapter_negotiated_w=None)
        self.assertEqual(flow_model(laptop(rated)).adapter_text, "—")
        measured = dataclasses.replace(CHARGING_TO_LIMIT, adapter_w=25.0)
        model = flow_model(laptop(measured))
        self.assertEqual((model.adapter_text, model.system_text), ("25.0 W", "7.0 W"))

    def test_idle_and_mixed(self):
        idle = BatterySnapshot(percent=80, ac_online=True, battery_w=0.4, limit=80)
        model = flow_model(laptop(idle))
        self.assertEqual((model.battery_title, model.battery_active), ("電池閒置", False))
        weak = BatterySnapshot(percent=40, ac_online=True, battery_w=-10.0,
                               adapter_negotiated_w=5.0)
        model = flow_model(laptop(weak))
        self.assertEqual((model.battery_title, model.battery_text, model.system_text),
                         ("電池放電", "10.0 W", "—"))
        self.assertFalse(model.battery_charging)


class Recap(unittest.TestCase):
    def test_numbers(self):
        recap = DailyRecap(used_percent=36, charged_percent=30, plug_in_count=1,
                           unplugged_minutes=280, longest_unplugged_minutes=45, start_percent=60,
                           end_percent=54)
        text = recap_text(recap)
        self.assertEqual(text.headline, "今天用掉 36%，補回 30%")
        self.assertEqual(text.metrics, (("用掉", "36%"), ("補回", "30%"), ("淨變化", "-6%"),
                                        ("接電次數", "1 次"), ("使用電池總時間", "4 小時 40 分"),
                                        ("最長一次", "45 分鐘")))

    def test_empty_and_gain(self):
        self.assertEqual(recap_text(DailyRecap()).headline, "今天還沒有紀錄")
        self.assertEqual(recap_text(DailyRecap()).metrics[2], ("淨變化", "—"))
        gain = DailyRecap(start_percent=40, end_percent=45, unplugged_minutes=120)
        self.assertEqual(recap_text(gain).metrics[2], ("淨變化", "+5%"))
        self.assertEqual(recap_text(gain).metrics[4], ("使用電池總時間", "2 小時"))


class Tray(unittest.TestCase):
    def test_label_and_menu(self):
        power = laptop(ON_BATTERY, system=SystemTimes(reports=True, to_empty_min=205))
        self.assertEqual(tray_label(power), "82%")
        self.assertEqual(menu_line(power), "電池：82%・還能用 3 小時 25 分")
        at_limit = BatterySnapshot(percent=80, ac_online=True, battery_w=0.0, limit=80)
        self.assertEqual(menu_line(laptop(at_limit)), "電池：80%・已達上限 80%")
        self.assertEqual(tray_label(laptop(dataclasses.replace(ON_BATTERY, percent=None))), "—%")

    def test_icon_names_follow_gnome(self):
        def icon(percent, state=None, **fields):
            snapshot = BatterySnapshot(percent=percent, battery_w=-5.0, **fields)
            return tray_icon(laptop(snapshot, state=state))
        self.assertEqual(icon(82), "battery-level-80-symbolic")
        self.assertEqual(icon(100), "battery-level-100-symbolic")
        self.assertEqual(icon(19), "battery-level-10-symbolic")
        self.assertEqual(icon(9), "battery-caution-symbolic")
        charging = dict(ac_online=True, charging_flag=True)
        self.assertEqual(icon(62, CHARGING, **charging), "battery-level-60-charging-symbolic")
        self.assertEqual(icon(5, CHARGING, **charging), "battery-caution-charging-symbolic")
        self.assertEqual(icon(100, CHARGING, **charging), "battery-level-100-charged-symbolic")
        self.assertEqual(icon(99, FULL, ac_online=True, full_flag=True),
                         "battery-level-100-charged-symbolic")
        self.assertEqual(icon(80, LIMIT_REACHED, ac_online=True, limit=80),
                         "battery-level-80-symbolic")
        self.assertEqual(icon(None), "battery-missing-symbolic")


if __name__ == "__main__":
    unittest.main()
