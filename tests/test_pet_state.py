"""桌面寵物的位置：存在 XDG 狀態目錄的小檔案，壞掉或不合理的內容一律忽略；示範模式不讀寫。"""
import json
from pathlib import Path
import tempfile
import unittest

from sdm.services.pet_state import PetPosition


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


if __name__ == "__main__":
    unittest.main()
