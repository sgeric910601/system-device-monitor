"""視窗狀態（XDG 狀態目錄的 state.json）：記住最後一頁，只在改變時寫入。"""
import json
from pathlib import Path
import tempfile
import unittest

from sdm.services.ui_state import PageMemory

PAGES = ("power", "overview", "cpu")


class Memory(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/"state/system-device-monitor/state.json"

    def test_missing_file_means_no_page_and_nothing_is_created(self):
        memory = PageMemory(self.path, PAGES)
        self.assertIsNone(memory.load())
        self.assertFalse(self.path.parent.exists())

    def test_saves_only_changes(self):
        memory = PageMemory(self.path, PAGES)
        self.assertTrue(memory.save("cpu"))
        self.assertEqual(json.loads(self.path.read_text()), {"page": "cpu"})
        inode = self.path.stat().st_ino
        self.assertFalse(memory.save("cpu"))
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(PageMemory(self.path, PAGES).load(), "cpu")

    def test_unknown_or_damaged_values_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"page": "../etc"}))
        self.assertIsNone(PageMemory(self.path, PAGES).load())
        self.path.write_text("{broken")
        self.assertIsNone(PageMemory(self.path, PAGES).load())
        self.assertFalse(PageMemory(self.path, PAGES).save("settings"))

    def test_disabled_memory_never_touches_the_disk(self):
        memory = PageMemory(self.path, PAGES, enabled=False)
        self.assertFalse(memory.save("cpu"))
        self.assertIsNone(memory.load())
        self.assertFalse(self.path.parent.exists())

    def test_write_failure_is_logged_once(self):
        self.path.parent.parent.mkdir(parents=True)
        self.path.parent.parent.chmod(0o500)
        self.addCleanup(self.path.parent.parent.chmod, 0o700)
        memory = PageMemory(self.path, PAGES)
        with self.assertLogs("sdm.services.ui_state", "WARNING"):
            self.assertFalse(memory.save("cpu"))


if __name__ == "__main__":
    unittest.main()
