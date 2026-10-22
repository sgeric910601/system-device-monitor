"""設定視窗的文字（不載入 GTK）：省電說明、更新頻率摘要、監控說明、筆電與桌機的用詞、說明文件位置。"""
from pathlib import Path
import tempfile
import unittest

from sdm.core.schedule import cadence
from sdm.core.settings import Preferences
from sdm.ui.settings import text


class Notes(unittest.TestCase):
    def test_power_saving_note_follows_the_reference(self):
        base = "省電模式：電力流向改用靜態圖、不播放動畫，更新頻率至少每 2 秒。"
        prefs = Preferences.defaults()
        self.assertEqual(text.power_saving_note(prefs, True), base + "更新越慢越省電。")
        on_ac = prefs.with_changes(power_saving=True, power_saving_on_battery_only=True)
        self.assertEqual(text.power_saving_note(on_ac, True), base + "目前接著電源，已暫時恢復一般模式。")
        fast = prefs.with_changes(power_saving=True, refresh_interval=1)
        self.assertEqual(text.power_saving_note(fast, False), base + "（目前每 2 秒更新）")

    def test_interval_titles(self):
        self.assertEqual([title for _, title in text.INTERVALS],
                         ["每秒更新", "每 2 秒更新", "每 5 秒更新", "每 10 秒更新"])

    def test_cadence_summary(self):
        self.assertEqual(text.cadence_note(cadence(2, False)),
                         "CPU／記憶體／網速：每 2 秒・溫度／不斷電系統／電池：每 5 秒・"
                         "耗電歷史：每分鐘記錄一次")
        self.assertIn("每秒", text.cadence_note(cadence(1, False)))

    def test_monitor_note(self):
        off = Preferences.defaults().with_changes(system_monitors=False)
        self.assertEqual(text.monitors_note(off, laptop=True),
                         "關閉時頂端列只有電源項目，四個模組都不會取樣，最省電。")
        self.assertIn("耗電", text.monitors_note(off, laptop=False))
        self.assertIn("立即停止取樣", text.monitors_note(Preferences.defaults(), laptop=False))


class Wording(unittest.TestCase):
    def test_scope_choices_for_laptops_and_desktops(self):
        self.assertEqual(text.scope_choices(laptop=True),
                         ((False, "只看電源"), (True, "電源＋系統監控")))
        self.assertEqual(text.scope_choices(laptop=False),
                         ((False, "只看耗電"), (True, "耗電＋系統監控")))

    def test_display_formats(self):
        self.assertEqual([value for value, _ in text.DISPLAY_FORMATS],
                         ["icon_percent", "icon_only", "percent_only", "icon_time",
                          "icon_percent_time"])
        self.assertEqual(text.DISPLAY_FORMATS[4][1], "圖示內百分比＋剩餘時間")


class Docs(unittest.TestCase):
    def test_source_checkout_first_then_the_package_location(self):
        with tempfile.TemporaryDirectory() as temp:
            root, package = Path(temp)/"src", Path(temp)/"doc"
            (root/"docs").mkdir(parents=True)
            (root/"docs/USAGE.md").write_text("x")
            package.mkdir()
            (package/"README.md").write_text("x")
            links = dict((title, path) for title, path in
                         text.doc_links(root, package))
            self.assertEqual(links["使用說明"], root/"docs/USAGE.md")
            self.assertEqual(links["專案說明"], package/"README.md")
            self.assertNotIn("隱私說明", links)


if __name__ == "__main__":
    unittest.main()
