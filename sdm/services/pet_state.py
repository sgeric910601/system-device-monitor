"""桌面寵物的位置：XDG 狀態目錄的 pet.json，內容是整數的 x 與 y，不是設定。

只在位置改變時寫入；壞掉、型別不對或大得不合理的內容一律忽略（改用預設位置）。
示範模式與模擬測試停用：完全不讀寫。寫入失敗只記錄，不影響寵物。
"""
import logging
from pathlib import Path

from sdm import paths
from sdm.constants import Pet
from sdm.core.jsonfile import load_json, write_json_atomic

LOG = logging.getLogger(__name__)
LIMIT = 1_000_000               # 座標的合理範圍（px）


def _coordinate(value):
    return value if type(value) is int and -LIMIT <= value <= LIMIT else None


class PetPosition:
    def __init__(self, path=None, enabled=True):
        self.path = Path(path) if path is not None else paths.STATE/Pet.STATE_FILE
        self.enabled, self.saved = enabled, None

    def load(self):
        if not self.enabled:
            return None
        stored = load_json(self.path)
        x, y = _coordinate(stored.get("x")), _coordinate(stored.get("y"))
        self.saved = None if x is None or y is None else (x, y)
        return self.saved

    def save(self, x, y):
        """寫入成功時回傳 True；停用或位置沒有改變時不寫。"""
        position = (int(x), int(y))
        if not self.enabled or position == self.saved:
            return False
        try:
            write_json_atomic(self.path, {"x": position[0], "y": position[1]})
        except OSError as error:
            LOG.warning("Cannot save the desktop pet position: %s", type(error).__name__)
            return False
        self.saved = position
        return True
