"""設定的「充電」分類與電源頁智慧充電那一列的註冊：位置、模擬測試會開啟它、
設定內容的欄位，以及顯示用的紀錄檔位置不出現帳號名稱。實際的畫面由 test_preview_run 在
虛擬螢幕上開啟（筆電示範有「充電」，桌機示範沒有）。"""
import dataclasses
from pathlib import Path
import unittest

from sdm.ui import settings_window, smoke
from sdm.ui.settings import charging


class Registration(unittest.TestCase):
    def test_section_sits_right_after_appearance(self):
        keys = [key for key, _, _ in settings_window.SECTIONS]
        self.assertEqual(keys[keys.index("appearance") + 1], "charging")
        titles = {key: title for key, title, _ in settings_window.SECTIONS}
        modules = {key: module for key, _, module in settings_window.SECTIONS}
        self.assertEqual((titles["charging"], modules["charging"]), ("充電", charging))

    def test_smoke_test_opens_and_captures_the_section(self):
        self.assertIn(("settings", "charging"), smoke.STEPS)
        self.assertIn("charging", smoke.LOWER_CAPTURES)

    def test_context_carries_the_plan_actions_only_for_laptops(self):
        fields = {field.name: field for field in dataclasses.fields(settings_window.SettingsContext)}
        self.assertIsNone(fields["charging"].default)

    def test_log_location_hides_the_home_directory(self):
        inside = Path.home()/".local/state/system-device-monitor/charging-plan.log"
        self.assertEqual(charging.home_relative(inside),
                         "~/.local/state/system-device-monitor/charging-plan.log")
        self.assertEqual(charging.home_relative("/var/tmp/x.log"), "/var/tmp/x.log")


if __name__ == "__main__":
    unittest.main()
