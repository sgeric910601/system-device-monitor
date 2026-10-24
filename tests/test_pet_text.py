"""由耗電快照產生夥伴的表情與桌面寵物的文字（筆電與桌機、含不斷電系統改用電池）。"""
import unittest

from sdm.core.companion import CHARGING_MOOD, IDLE, LOW, CompanionState
from sdm.core.pet_messages import DesktopPetStatus
from sdm.core.recap import DailyRecap
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services import preview
from sdm.services.power_service import PowerService
from sdm.ui.pet_text import companion_state, pet_status, sayings_for, thought_for


def laptop(status="Discharging", capacity=82, power_w=15.0):
    battery = BatteryReading(name="BAT0", status=status, capacity=capacity, power_w=power_w,
                             energy_full_wh=50.0, energy_design_wh=55.0, cycle_count=120)
    return PowerService(read_ups=lambda: None, read_gpus=lambda: (), read_uptime=lambda: None,
                        wall=lambda: 1_800_000_000.0, read_batteries=lambda: (battery,),
                        read_upower=lambda: None).sample()


def desktop(on_battery=False):
    ups = UpsReading(status=("ONBATT",) if on_battery else ("ONLINE",), online=not on_battery,
                     on_battery=on_battery, charge_pct=85.0 if on_battery else 100.0,
                     load_pct=32.0, load_w=288.0, time_left_min=44.0 if on_battery else None)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0).sample()


class State(unittest.TestCase):
    def test_laptop_on_battery_rests(self):
        self.assertEqual(companion_state(laptop(), 20), CompanionState(IDLE, 0.82))

    def test_laptop_low_battery_worries(self):
        self.assertEqual(companion_state(laptop(capacity=15), 20).mood, LOW)

    def test_desktop_on_mains_and_on_ups_battery(self):
        self.assertEqual(companion_state(desktop(), 20), CompanionState(IDLE, 1.0))
        self.assertEqual(companion_state(desktop(on_battery=True), 20).mood, LOW)

    def test_no_snapshot_yet(self):
        self.assertEqual(companion_state(None, 20), CompanionState())

    def test_preview_charging_lies_on_the_pad(self):
        power = preview.power_service("laptop-charging").sample()
        self.assertEqual(companion_state(power, 20).mood, CHARGING_MOOD)


class Text(unittest.TestCase):
    def test_laptop_thought_and_sayings(self):
        power = laptop()
        self.assertTrue(thought_for(power, 20).startswith("82%・"))
        recap = DailyRecap(used_percent=14, charged_percent=3, start_percent=90, end_percent=82)
        lines = sayings_for(power, 20, recap)
        self.assertEqual(lines[1], "今天用掉 14%，補回 3%")
        self.assertEqual(lines[2], "電池健康度 91%，充過 120 次")
        self.assertEqual(lines[3], "現在整台電腦用了 15.0 W")

    def test_unknown_battery_level_shows_no_percentage(self):
        power = laptop(capacity=None)
        self.assertIsNone(power.percent)
        self.assertNotIn("0%", thought_for(power, 20))
        self.assertFalse(thought_for(power, 20)[0].isdigit())
        self.assertNotIn("0%", " ".join(sayings_for(power, 20, None)))

    def test_laptop_without_today_skips_that_line(self):
        lines = sayings_for(laptop(), 20, DailyRecap())
        self.assertNotIn("今天", " ".join(lines))

    def test_desktop_thought_and_sayings(self):
        self.assertEqual(thought_for(desktop(), 20), "整機 288 W・市電")
        self.assertEqual(sayings_for(desktop(), 20, None),
                         ["插著電在休息", "現在整台電腦用了 288.0 W", "不斷電系統電量 100%，負載 32%"])

    def test_desktop_on_ups_battery(self):
        power = desktop(on_battery=True)
        self.assertEqual(thought_for(power, 20),
                         "整機 288 W・不斷電系統電池\n不斷電系統 85%・還能撐 44 分鐘")
        self.assertEqual(sayings_for(power, 20, None)[0], "停電了！不斷電系統還能撐 44 分鐘")
        status = pet_status(power, 20)
        self.assertIsInstance(status, DesktopPetStatus)
        self.assertTrue(status.on_ups_battery)

    def test_preview_desktop_matches_the_demo_ups(self):
        power = preview.power_service("desktop").sample()
        self.assertEqual(thought_for(power, 20), "整機 288 W・市電")

    def test_no_snapshot_has_no_text(self):
        self.assertIsNone(thought_for(None, 20))
        self.assertEqual(sayings_for(None, 20, None), [])


if __name__ == "__main__":
    unittest.main()
