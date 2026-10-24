"""桌面寵物的想像框與對話（移植參考專案 DesktopPetTests），以及桌機與不斷電系統的說法。"""
from dataclasses import replace
import unittest

from sdm.core.battery import CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE
from sdm.core.pet_messages import (PURRS, DesktopPetStatus, PetStatus, desktop_sayings,
                                   desktop_thought, duration, mood, sayings, thought)


class Thought(unittest.TestCase):
    def test_shows_state_and_plan(self):
        status = PetStatus(percent=62, state=CHARGING, minutes_to_full=36, limit=80)
        self.assertEqual(thought(status), "62%・充電中，36 分鐘後到 80%")
        status = replace(status, limit=None, minutes_to_full=95)
        self.assertEqual(thought(status), "62%・充電中，1 小時 35 分後充滿")
        battery = PetStatus(percent=78, state=DISCHARGING, minutes_to_empty=445)
        self.assertEqual(thought(battery), "78%・還能用 7 小時 25 分")
        self.assertEqual(thought(replace(battery, minutes_to_empty=None)), "78%・使用電池中")
        plan = PetStatus(percent=80, state=LIMIT_REACHED, plan_title="等待開始",
                         plan_detail="明天 5:54 開始充電，明天 7:30 前到 95%")
        self.assertEqual(thought(plan), "80%・已達充電上限，用變壓器供電\n"
                                        "等待開始：明天 5:54 開始充電，明天 7:30 前到 95%")

    def test_other_states(self):
        self.assertEqual(thought(PetStatus(percent=100, state=FULL)), "100%・已充飽")
        self.assertEqual(thought(PetStatus(percent=50, state=CHARGING)), "50%・充電中")
        idle = PetStatus(percent=70, state=PLUGGED_IDLE)
        self.assertEqual(thought(idle), "70%・接著電源，暫停充電")
        self.assertEqual(thought(replace(idle, draining_on_ac=True)), "70%・供電不足，電池在放電")
        plan = PetStatus(percent=70, state=PLUGGED_IDLE, plan_title="智慧充電")
        self.assertEqual(thought(plan), "70%・接著電源，暫停充電\n智慧充電")

    def test_duration_has_at_least_one_minute(self):
        self.assertEqual([duration(value) for value in (0, 59, 60, 61, 125)],
                         ["1 分鐘", "59 分鐘", "1 小時", "1 小時 1 分", "2 小時 5 分"])


class Sayings(unittest.TestCase):
    def test_follow_mood_and_data(self):
        low = PetStatus(percent=8, state=DISCHARGING)
        self.assertEqual(sayings(low), ["快沒電了！拜託快接上電源"])
        self.assertEqual(mood(replace(low, percent=18)), "肚子有點餓，記得幫我充電")
        self.assertEqual(mood(replace(low, percent=55)), "陪你一起努力，還有 55%")
        draining = PetStatus(percent=15, state=PLUGGED_IDLE, draining_on_ac=True)
        self.assertEqual(mood(draining), "肚子有點餓，記得幫我充電")
        full = PetStatus(percent=95, state=LIMIT_REACHED, used_today=14, charged_today=3,
                         health_percent=92, cycle_count=212, system_watts=6.5)
        self.assertEqual(sayings(full), ["吃飽了，現在靠變壓器供電", "今天用掉 14%，補回 3%",
                                         "電池健康度 92%，充過 212 次", "現在整台電腦用了 6.5 W"])
        waiting = PetStatus(percent=80, state=LIMIT_REACHED, plan_waiting=True,
                            plan_detail="5:54 開始充電，7:30 前到 95%")
        self.assertEqual(mood(waiting), "我先睡一下，5:54 開始充電，7:30 前到 95%")
        self.assertEqual(mood(replace(waiting, plan_ready=True)), "準備好了，隨時可以出門！")
        self.assertEqual(mood(PetStatus(percent=60, state=CHARGING)), "充電中，好溫暖～")

    def test_missing_data_is_left_out(self):
        status = PetStatus(percent=60, state=PLUGGED_IDLE, used_today=5, health_percent=90,
                           system_watts=0.0)
        self.assertEqual(sayings(status), ["插著電在休息", "電池健康度 90%"])
        self.assertEqual(mood(PetStatus(percent=100, state=FULL)), "吃飽了，現在靠變壓器供電")
        self.assertEqual(mood(PetStatus(percent=30, state=DISCHARGING, low_threshold=30)),
                         "肚子有點餓，記得幫我充電")

    def test_unknown_percent_is_left_out(self):
        battery = PetStatus(percent=None, state=DISCHARGING, minutes_to_empty=60)
        self.assertEqual(thought(battery), "還能用 1 小時")
        self.assertEqual(mood(battery), "陪你一起努力")
        self.assertEqual(sayings(PetStatus(percent=None, state=CHARGING)), ["充電中，好溫暖～"])
        draining = PetStatus(percent=None, state=PLUGGED_IDLE, draining_on_ac=True)
        self.assertEqual(mood(draining), "陪你一起努力")
        for status in (battery, draining, PetStatus(percent=None, state=FULL)):
            self.assertNotIn("%", thought(status) + "".join(sayings(status)))

    def test_purrs_match_the_reference(self):
        self.assertEqual(PURRS, ("呼嚕呼嚕～", "好舒服喔", "再摸一下嘛", "喵～"))


class Desktop(unittest.TestCase):
    def test_thought_shows_total_watts_and_supply(self):
        self.assertEqual(desktop_thought(DesktopPetStatus(system_watts=288.0, supply="市電")),
                         "整機 288 W・市電")
        self.assertEqual(desktop_thought(DesktopPetStatus(system_watts=None, supply="市電")),
                         "整機耗電讀取中・市電")

    def test_thought_on_ups_battery_adds_the_backup_line(self):
        status = DesktopPetStatus(system_watts=250.4, supply="不斷電系統電池", on_ups_battery=True,
                                  ups_percent=85.0, ups_minutes=44)
        self.assertEqual(desktop_thought(status),
                         "整機 250 W・不斷電系統電池\n不斷電系統 85%・還能撐 44 分鐘")
        self.assertEqual(desktop_thought(replace(status, ups_minutes=None)),
                         "整機 250 W・不斷電系統電池\n不斷電系統 85%")

    def test_sayings_for_mains_ups_battery_and_busy(self):
        mains = DesktopPetStatus(system_watts=288.0, supply="市電", ups_percent=100.0,
                                 ups_load_pct=32.0)
        self.assertEqual(desktop_sayings(mains), ["插著電在休息", "現在整台電腦用了 288.0 W",
                                                  "不斷電系統電量 100%，負載 32%"])
        outage = replace(mains, on_ups_battery=True, ups_percent=85.0, ups_minutes=44)
        self.assertEqual(desktop_sayings(outage)[0], "停電了！不斷電系統還能撐 44 分鐘")
        self.assertEqual(desktop_sayings(replace(outage, ups_minutes=None))[0],
                         "停電了！先靠不斷電系統撐著")
        busy = DesktopPetStatus(system_watts=455.0, supply="市電", busy=True)
        self.assertEqual(desktop_sayings(busy), ["好忙喔，整機用了 455 W",
                                                 "現在整台電腦用了 455.0 W"])
        self.assertEqual(desktop_sayings(DesktopPetStatus(system_watts=None, supply="市電")),
                         ["插著電在休息"])


if __name__ == "__main__":
    unittest.main()
