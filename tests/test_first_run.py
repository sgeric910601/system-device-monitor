"""首次啟動的「監控範圍」選擇：只在舊版 settings.json 不存在時詢問，模擬測試與示範模式不問；
關閉視窗等於選「電源＋系統監控」（預設）。"""
import unittest

from sdm.core.settings import Preferences
from sdm.ui import first_run
from sdm.ui.first_run import (POWER_ONLY, WITH_MONITORS, apply_choice, choices, should_ask,
                              system_monitors_for)


class Decision(unittest.TestCase):
    def test_only_on_a_real_first_run(self):
        self.assertTrue(should_ask(first_run=True, smoke=False, preview=None))
        self.assertFalse(should_ask(first_run=False, smoke=False, preview=None))
        self.assertFalse(should_ask(first_run=True, smoke=True, preview=None))
        self.assertFalse(should_ask(first_run=True, smoke=False, preview="desktop"))

    def test_responses(self):
        self.assertFalse(system_monitors_for(POWER_ONLY))
        self.assertTrue(system_monitors_for(WITH_MONITORS))
        self.assertTrue(system_monitors_for(None))          # 直接關閉視窗

    def test_wording(self):
        self.assertEqual([title for _, title, _ in choices(laptop=True)],
                         ["只看電源", "電源＋系統監控"])
        self.assertEqual([title for _, title, _ in choices(laptop=False)],
                         ["只看耗電", "耗電＋系統監控"])

    def test_choice_goes_through_the_store(self):
        class Store:
            def __init__(self): self.calls = []
            def update(self, **changes): self.calls.append(changes)
        store = Store()
        apply_choice(store, POWER_ONLY)
        self.assertEqual(store.calls, [{"system_monitors": False}])

    def test_read_only_preferences_are_logged_not_raised(self):
        from sdm.core.settings import PreferencesReadOnlyError

        class Store:
            preferences = Preferences.defaults(read_only=True)
            def update(self, **changes): raise PreferencesReadOnlyError("newer")
        with self.assertLogs(first_run.LOG.name, "WARNING"):
            apply_choice(Store(), POWER_ONLY)


if __name__ == "__main__":
    unittest.main()
