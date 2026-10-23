"""設定視窗的「管理員功能」分類：註冊位置、模擬測試會開啟它，以及設定內容的欄位。"""
import dataclasses
import unittest

from sdm.ui import settings_window, smoke
from sdm.ui.settings import admin


class Registration(unittest.TestCase):
    def test_section_sits_before_about(self):
        keys = [key for key, _, _ in settings_window.SECTIONS]
        self.assertEqual(keys[-2:], ["admin", "about"])
        titles = {key: title for key, title, _ in settings_window.SECTIONS}
        self.assertEqual(titles["admin"], "管理員功能")
        self.assertIs(dict((key, module) for key, _, module in settings_window.SECTIONS)["admin"],
                      admin)

    def test_smoke_test_opens_the_section(self):
        self.assertIn("admin", smoke.SETTINGS_SECTIONS)
        self.assertIn(("settings", "admin"), smoke.STEPS)
        self.assertIn("admin", smoke.LOWER_CAPTURES)

    def test_context_carries_the_admin_components(self):
        fields = {field.name: field for field in dataclasses.fields(settings_window.SettingsContext)}
        self.assertIsNone(fields["admin"].default)


class Docs(unittest.TestCase):
    def test_about_page_links_the_security_notes(self):
        from sdm.ui.settings import text
        self.assertIn(("安全說明", "docs/SECURITY.md"), text.DOCS)


if __name__ == "__main__":
    unittest.main()
