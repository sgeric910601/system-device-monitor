"""充電夥伴的外觀循環與表情：筆電依電池狀態，桌機依不斷電系統與負載。"""
import unittest

from sdm.core.battery import CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE
from sdm.core.companion import (APPEARANCES, BUSY, CHARGING_MOOD, CRITICAL, FULL_MOOD, GOOD,
                                IDLE, LOW, NEUTRAL, TITLES, WARNING, CompanionState,
                                desktop_state, laptop_state, next_appearance)


class Appearance(unittest.TestCase):
    def test_order_and_titles(self):
        self.assertEqual(APPEARANCES, ("cat", "buddy", "bar", "headshot", "photo"))
        self.assertEqual([TITLES[name] for name in APPEARANCES],
                         ["貓咪", "電池夥伴", "電量條", "貓咪大頭照", "我的照片"])

    def test_click_cycles_and_skips_the_photo_until_one_is_chosen(self):
        self.assertEqual(next_appearance("cat", has_photo=False), "buddy")
        self.assertEqual(next_appearance("bar", has_photo=False), "headshot")
        self.assertEqual(next_appearance("headshot", has_photo=False), "cat")
        self.assertEqual(next_appearance("headshot", has_photo=True), "photo")
        self.assertEqual(next_appearance("photo", has_photo=True), "cat")
        self.assertEqual(next_appearance("photo", has_photo=False), "cat")
        self.assertEqual(next_appearance("unknown", has_photo=True), "cat")

    def test_full_cycle_visits_every_look_once(self):
        seen, current = [], "cat"
        for _ in APPEARANCES:
            seen.append(current)
            current = next_appearance(current, has_photo=True)
        self.assertEqual(seen, list(APPEARANCES))
        self.assertEqual(current, "cat")


class Laptop(unittest.TestCase):
    def test_charging_lies_on_the_pad(self):
        self.assertEqual(laptop_state(62, CHARGING, False, 20),
                         CompanionState(CHARGING_MOOD, 0.62, GOOD, on_pad=True))

    def test_full_and_limit_are_happy(self):
        for state in (FULL, LIMIT_REACHED):
            with self.subTest(state=state):
                self.assertEqual(laptop_state(80, state, False, 20),
                                 CompanionState(FULL_MOOD, 0.8, GOOD))

    def test_resting_on_battery_or_plugged_in(self):
        self.assertEqual(laptop_state(55, DISCHARGING, False, 20),
                         CompanionState(IDLE, 0.55, NEUTRAL))
        self.assertEqual(laptop_state(55, PLUGGED_IDLE, False, 20),
                         CompanionState(IDLE, 0.55, GOOD))

    def test_low_battery_worries(self):
        self.assertEqual(laptop_state(18, DISCHARGING, False, 20),
                         CompanionState(LOW, 0.18, WARNING))
        self.assertEqual(laptop_state(10, DISCHARGING, False, 20),
                         CompanionState(LOW, 0.1, CRITICAL))
        self.assertEqual(laptop_state(60, PLUGGED_IDLE, True, 20),
                         CompanionState(LOW, 0.6, WARNING))

    def test_unknown_percent_has_no_level(self):
        self.assertIsNone(laptop_state(None, DISCHARGING, False, 20).level)

    def test_resting_states_sleep(self):
        self.assertTrue(CompanionState(IDLE).resting)
        self.assertFalse(CompanionState(CHARGING_MOOD, on_pad=True).resting)
        self.assertFalse(CompanionState(LOW).resting)


class Desktop(unittest.TestCase):
    def test_mains_power_rests_and_shows_the_ups_charge(self):
        self.assertEqual(desktop_state(False, 100.0, 32.0, 288.0),
                         CompanionState(IDLE, 1.0, NEUTRAL))
        self.assertEqual(desktop_state(False, None, None, 120.0), CompanionState(IDLE, None, NEUTRAL))

    def test_ups_on_battery_worries(self):
        self.assertEqual(desktop_state(True, 85.0, 40.0, 300.0), CompanionState(LOW, 0.85, WARNING))
        self.assertEqual(desktop_state(True, 8.0, 40.0, 300.0), CompanionState(LOW, 0.08, CRITICAL))

    def test_high_load_is_busy(self):
        self.assertEqual(desktop_state(False, 100.0, 75.0, 600.0).mood, BUSY)
        self.assertEqual(desktop_state(False, None, None, 420.0).mood, BUSY)
        self.assertEqual(desktop_state(False, 100.0, 40.0, 420.0).mood, IDLE)


if __name__ == "__main__":
    unittest.main()
