"""視窗狀態：記住主視窗最後顯示的頁面（XDG 狀態目錄的 state.json）。

不是設定：不寫進 settings.json 或 preferences.json。只在關閉視窗或結束時、
而且頁面確實改變時才寫入；示範模式與模擬測試完全不讀寫。
"""
import logging

from sdm.core.jsonfile import load_json, write_json_atomic

LOG = logging.getLogger(__name__)
PAGE = "page"


class PageMemory:
    def __init__(self, path, pages, enabled=True):
        self.path, self.pages, self.enabled = path, tuple(pages), enabled
        self.saved = None

    def load(self):
        if not self.enabled:
            return None
        page = load_json(self.path).get(PAGE)
        self.saved = page if page in self.pages else None
        return self.saved

    def save(self, page):
        """寫入成功時回傳 True；未知的頁面、沒有改變或停用時不寫。"""
        if not self.enabled or page not in self.pages or page == self.saved:
            return False
        try:
            write_json_atomic(self.path, {PAGE: page})
        except OSError as error:
            LOG.warning("Cannot save the window state: %s", type(error).__name__)
            return False
        self.saved = page
        return True
