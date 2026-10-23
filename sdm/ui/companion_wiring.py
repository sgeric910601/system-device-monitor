"""看板的充電夥伴（Monitor 的混入類別）：電源頁上的夥伴與桌面寵物共用的狀態。

- 表情跟著最新的耗電快照（筆電電池狀態；桌機的不斷電系統與負載），每次收到資料都更新，
  沒有改變就不重畫。
- 點一下夥伴換下一個外觀（寫入 companion_appearance）；右鍵選單選特定外觀或打開設定。
- 外觀、會動的眼睛、眼睛跟著游標與低電量門檻改變時立即套用。
- 動畫只在主視窗看得到（沒有隱藏、最小化或被完全遮住）而且省電模式沒有生效時播放。
- 示範模式與模擬測試不讀使用者的照片（PhotoStore 停用）。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging

from sdm.core.companion import next_appearance
from sdm.core.settings import PreferencesReadOnlyError
from sdm.services.companion_photo import PhotoStore
from sdm.ui.companion import CompanionView
from sdm.ui.pet_text import companion_state
from sdm.ui.sprite import Images

LOG = logging.getLogger(__name__)
FIGURE_KEYS = frozenset({"companion_appearance", "desktop_pet_live_eyes",
                         "desktop_pet_follow_cursor"})
STATE_KEYS = frozenset({"low_threshold"})
SETTINGS_SECTION = "companion"


class CompanionMixin:
    companion = None            # 電源頁上的夥伴（CompanionView）
    photos = None               # 我的照片（PhotoStore）
    companion_images = None     # 夥伴與桌面寵物共用的圖片
    pet = None                  # 桌面寵物（之後加入）

    def build_companion(self):
        self.photos = PhotoStore(enabled=not (self.preview or self.smoke))
        self.companion_images = Images()
        view = CompanionView(self.photos, self.companion_images, interactive=True)
        view.on_select = self.select_appearance
        view.on_settings = lambda: self.open_settings(SETTINGS_SECTION)
        self.companion = view
        self.power_page.set_companion(view)
        self.apply_companion_settings()

    def companion_views(self):
        return [view for view in (self.companion,) if view is not None]

    def select_appearance(self, name):
        """name 為 None 時換下一個外觀；設定唯讀時維持原本的外觀。"""
        if name is None:
            name = next_appearance(self.preferences.companion_appearance,
                                   self.photos is not None and self.photos.has_photo())
        try:
            self.store.update(companion_appearance=name)
        except (PreferencesReadOnlyError, KeyError, ValueError) as error:
            LOG.warning("Cannot change the companion: %s", type(error).__name__)

    def apply_companion_settings(self):
        prefs = self.preferences
        for view in self.companion_views():
            view.configure(appearance=prefs.companion_appearance,
                           live_eyes=prefs.desktop_pet_live_eyes,
                           follow_cursor=prefs.desktop_pet_follow_cursor)

    def update_companions(self):
        """收到新資料：依最新的耗電快照更新表情（表情沒變時元件不重畫）。"""
        state = companion_state(self.current_power(), self.preferences.low_threshold)
        for view in self.companion_views():
            view.configure(state=state)

    def update_companion_motion(self):
        if self.companion is None:
            return
        visible = self.window_shown() and not self.window_obscured
        self.companion.set_motion(visible, self.cadence.saving)

    def companion_settings_changed(self, changed):
        if changed & FIGURE_KEYS:
            self.apply_companion_settings()
        if changed & (FIGURE_KEYS | STATE_KEYS):
            self.update_companions()
