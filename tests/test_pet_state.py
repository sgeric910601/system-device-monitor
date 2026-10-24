"""桌面寵物的位置：存在 XDG 狀態目錄的小檔案，壞掉或不合理的內容一律忽略；示範模式不讀寫。"""
import json
from pathlib import Path
import tempfile
import unittest

from sdm.core.pet_geometry import Rect
from sdm.services.pet_state import PetPosition, PositionKeeper

SCREEN = (Rect(0, 0, 1920, 1080),)


class Position(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/"state/system-device-monitor/pet.json"

    def test_round_trip(self):
        PetPosition(self.path).save(120, -40)
        self.assertEqual(json.loads(self.path.read_text()), {"x": 120, "y": -40})
        self.assertEqual(PetPosition(self.path).load(), (120, -40))

    def test_same_position_is_not_written_again(self):
        position = PetPosition(self.path)
        self.assertTrue(position.save(10, 20))
        self.assertFalse(position.save(10, 20))
        self.assertTrue(position.save(11, 20))

    def test_missing_or_invalid_files_give_no_position(self):
        self.assertIsNone(PetPosition(self.path).load())
        self.path.parent.mkdir(parents=True)
        for text in ("not json", "[]", '{"x": "1", "y": 2}', '{"x": true, "y": 2}',
                     '{"x": 1}', '{"x": 1e12, "y": 0}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertIsNone(PetPosition(self.path).load())

    def test_disabled_never_reads_or_writes(self):
        PetPosition(self.path).save(1, 2)
        disabled = PetPosition(self.path, enabled=False)
        self.assertIsNone(disabled.load())
        self.assertFalse(disabled.save(5, 6))
        self.assertEqual(PetPosition(self.path).load(), (1, 2))

    def test_write_failure_is_logged_not_raised(self):
        self.path.parent.parent.mkdir(parents=True)
        self.path.parent.write_text("a file where the folder should be")
        with self.assertLogs("sdm.services.pet_state", "WARNING"):
            self.assertFalse(PetPosition(self.path).save(1, 2))


class Keeper(unittest.TestCase):
    """只在使用者拖曳（或改大小）之後才記住位置；視窗第一次擺好、螢幕改變時不寫檔。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/"pet.json"
        self.keeper = PositionKeeper(PetPosition(self.path))

    def test_first_placement_is_not_saved(self):
        self.assertEqual(self.keeper.settle(Rect(100, 200, 176, 176), SCREEN, (0, 0)), (100, 200))
        self.assertFalse(self.path.exists())

    def test_position_after_a_drag_is_saved_once(self):
        self.keeper.user_moved()
        self.keeper.settle(Rect(300, 400, 176, 176), SCREEN, (0, 0))
        self.assertEqual(PetPosition(self.path).load(), (300, 400))
        self.path.unlink()
        self.keeper.settle(Rect(310, 400, 176, 176), SCREEN, (0, 0))
        self.assertFalse(self.path.exists())

    def test_clamped_position_is_what_gets_saved(self):
        self.keeper.user_moved()
        self.assertEqual(self.keeper.settle(Rect(1900, 1060, 176, 176), SCREEN, (0, 0)),
                         (1872, 1032))
        self.assertEqual(PetPosition(self.path).load(), (1872, 1032))

    def test_without_a_store_nothing_is_saved(self):
        keeper = PositionKeeper(None)
        keeper.user_moved()
        self.assertEqual(keeper.settle(Rect(5, 6, 176, 176), SCREEN, (0, 0)), (5, 6))


if __name__ == "__main__":
    unittest.main()
