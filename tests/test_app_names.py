"""App 代號 → 顯示名稱與圖示：查桌面檔、快取、特殊項目與示範名稱（代號都是虛構的）。"""
import unittest

from sdm.core.apps import SESSION_ID, SYSTEM_ID, AppsSample, AppUsage
from sdm.core.snapshots import AppsDetail
from sdm.ui import app_names
from sdm.ui.app_names import AppNames


class Resolve(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def lookup(app_id):
            self.calls.append(app_id)
            return {"org.example.Editor": ("Example\x07 Editor", "editor-icon",
                                           "org.example.Editor.desktop")}.get(app_id)
        self.clock = [1000.0]
        self.names = AppNames(lookup, clock=lambda: self.clock[0])

    def test_desktop_file_name_is_cleaned_and_cached(self):
        self.assertEqual(self.names.resolve("org.example.Editor"), ("Example Editor", "editor-icon"))
        self.names.resolve("org.example.Editor")
        self.assertEqual(self.calls, ["org.example.Editor"])

    def test_unknown_apps_fall_back_to_their_id(self):
        self.assertEqual(self.names.resolve("example-helper"),
                         ("example-helper", app_names.DEFAULT_ICON))
        self.names.resolve("example-helper")
        self.assertEqual(self.calls, ["example-helper"])          # 找不到也快取

    def test_unknown_apps_are_looked_up_again_after_ten_minutes(self):
        # 剛安裝的 App 桌面檔可能稍後才出現：找不到的結果只快取 10 分鐘
        self.names.resolve("example-helper")
        self.clock[0] += app_names.NEGATIVE_TTL_SECONDS - 1
        self.names.resolve("example-helper")
        self.assertEqual(self.calls, ["example-helper"])
        self.clock[0] += 1
        self.names.resolve("example-helper")
        self.assertEqual(self.calls, ["example-helper", "example-helper"])
        self.assertEqual(app_names.NEGATIVE_TTL_SECONDS, 600)
        self.names.resolve("org.example.Editor")
        self.clock[0] += 10 * app_names.NEGATIVE_TTL_SECONDS
        self.names.resolve("org.example.Editor")
        self.assertEqual(self.calls.count("org.example.Editor"), 1)   # 找得到的一直沿用

    def test_special_entries(self):
        self.assertEqual(self.names.resolve(SESSION_ID)[0], "GNOME 桌面")
        self.assertEqual(self.names.resolve(SYSTEM_ID)[0], "系統服務")
        self.assertEqual(self.calls, [])

    def test_labels_supplied_with_the_data(self):
        labels = (("demo.browser", "示範瀏覽器", "web-browser"),)
        self.assertEqual(self.names.resolve("demo.browser", labels), ("示範瀏覽器", "web-browser"))
        self.assertEqual(self.calls, [])

    def test_name_for_is_usable_for_ranking(self):
        name_for = self.names.name_for(())
        self.assertEqual(name_for("org.example.Editor"), "Example Editor")

    def test_missing_desktop_file_with_the_real_lookup(self):
        self.assertIsNone(app_names.desktop_lookup("org.example.DoesNotExist12345"))
        self.assertIsNone(app_names.desktop_lookup("../x"))


DESKTOP_FILES = {
    "example-browser": ("Example Browser", "web-browser", "example-browser.desktop"),
    "com.example.Browser": ("Example Browser", "web-browser", "com.example.Browser.desktop"),
    "org.example.Editor": ("Example Editor", "editor", "org.example.Editor.desktop"),
    "org.example.editor": ("Example Editor", "editor", "org.example.Editor.desktop"),
}


class SameName(unittest.TestCase):
    """同一個桌面檔（多個視窗或啟動方式）合併；不同桌面檔只是名稱相同時分開列出並加註。"""

    def rows(self, values):
        usages = tuple(AppUsage(app_id, value, value / 4, None) for app_id, value in values)
        detail = AppsDetail(AppsSample(usages, True), 4)
        return app_names.top_rows(detail, AppNames(DESKTOP_FILES.get), "cpu", 4)

    def test_same_desktop_file_is_merged(self):
        rows = self.rows((("org.example.Editor", 30.0), ("org.example.editor", 20.0)))
        self.assertEqual([(row[0], row[2]) for row in rows], [("Example Editor", "50.0%")])

    def test_different_desktop_files_with_the_same_name_stay_apart(self):
        rows = self.rows((("example-browser", 30.0), ("com.example.Browser", 20.0),
                          ("org.example.Editor", 10.0)))
        self.assertEqual([row[0] for row in rows], ["Example Browser（example-browser）",
                                                    "Example Browser（Browser）",
                                                    "Example Editor"])


class TopRows(unittest.TestCase):
    def detail(self, ready=True, has_cpu=True):
        usages = (AppUsage("org.example.Editor", 30.0, 1.0, 300), AppUsage("example-helper", 50.0, 2.0, 100),
                  AppUsage("demo.browser", None, None, 900), AppUsage(SESSION_ID, 5.0, 0.1, 50))
        return AppsDetail(AppsSample(usages, ready), 32, has_cpu=has_cpu,
                          labels=(("demo.browser", "示範瀏覽器", "web-browser"),))

    def test_cpu_and_memory_rows(self):
        names = AppNames(lambda app_id: None)
        cpu = app_names.top_rows(self.detail(), names, "cpu", 32)
        self.assertEqual([row[0] for row in cpu], ["example-helper", "org.example.Editor",
                                                   "GNOME 桌面"])
        self.assertEqual(cpu[0][2:], ("50.0%", "全機 1.6%"))
        memory = app_names.top_rows(self.detail(), names, "memory", 32)
        self.assertEqual([(row[0], row[1]) for row in memory][:2],
                         [("示範瀏覽器", "web-browser"), ("org.example.Editor", app_names.DEFAULT_ICON)])

    def test_placeholder_states(self):
        names = AppNames(lambda app_id: None)
        self.assertEqual(app_names.placeholder(None, "cpu"), "計算中…")
        self.assertEqual(app_names.placeholder(self.detail(ready=False), "cpu"), "計算中…")
        self.assertEqual(app_names.placeholder(self.detail(), "cpu"), "目前沒有明顯的 CPU 使用量")
        self.assertEqual(app_names.placeholder(self.detail(), "memory"), "目前沒有可顯示的 App")


if __name__ == "__main__":
    unittest.main()
