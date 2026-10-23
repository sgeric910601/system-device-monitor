"""看板的充電夥伴（Monitor 的混入類別）：電源頁上的夥伴與桌面寵物。

- 表情跟著最新的耗電快照（筆電電池狀態；桌機的不斷電系統與負載），每次收到資料都更新，
  沒有改變就不重畫。
- 點一下夥伴換下一個外觀（寫入 companion_appearance）；右鍵選單選特定外觀或打開設定。
- 外觀、會動的眼睛、眼睛跟著游標與低電量門檻改變時立即套用。
- 動畫只在主視窗看得到（沒有隱藏、最小化或被完全遮住）而且省電模式沒有生效時播放。
- 桌面寵物依設定建立或關閉；位置（浮在視窗上或放在桌布上）與大小即時套用。
  寵物在主視窗隱藏時仍然看得到，所以只在省電模式時停止動畫。
- 示範模式與模擬測試不讀使用者的照片（PhotoStore 停用），也不讀寫寵物的位置。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging
import time

from sdm.core.companion import next_appearance
from sdm.core.settings import PreferencesReadOnlyError
from sdm.services.battery_service import LaptopPower
from sdm.services.companion_photo import PhotoStore
from sdm.services.pet_state import PetPosition
from sdm.ui.companion import CompanionView
from sdm.ui.pet import DesktopPet, PetHost, is_x11
from sdm.ui.settings.companion import CompanionContext
from sdm.ui.pet_text import companion_state, sayings_for, thought_for
from sdm.ui.sprite import Images

LOG = logging.getLogger(__name__)
FIGURE_KEYS = frozenset({"companion_appearance", "desktop_pet_live_eyes",
                         "desktop_pet_follow_cursor"})
STATE_KEYS = frozenset({"low_threshold"})
PET_KEYS = frozenset({"desktop_pet", "desktop_pet_on_wallpaper", "desktop_pet_scale"})
SETTINGS_SECTION = "companion"
PET_SECTION = "pet"
POWER_PAGE = "power"


class CompanionMixin:
    companion = None            # 電源頁上的夥伴（CompanionView）
    photos = None               # 我的照片（PhotoStore）
    companion_images = None     # 夥伴與桌面寵物共用的圖片
    pet = None                  # 桌面寵物（DesktopPet）；關閉時為 None
    pet_factory = DesktopPet

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
        pet_view = self.pet.view if self.pet is not None else None
        return [view for view in (self.companion, pet_view) if view is not None]

    def update_preferences(self, **changes):
        """夥伴與寵物的選單寫入設定；設定唯讀或值無效時維持原本的設定。"""
        try:
            self.store.update(**changes)
        except (PreferencesReadOnlyError, KeyError, ValueError) as error:
            LOG.warning("Cannot change %s: %s", ",".join(changes), type(error).__name__)

    def select_appearance(self, name):
        """點一下（name 為 None）換下一個外觀，選單則指定外觀。"""
        if name is None:
            name = next_appearance(self.preferences.companion_appearance,
                                   self.photos is not None and self.photos.has_photo())
        self.update_preferences(companion_appearance=name)

    def pet_sayings(self):
        power, now = self.current_power(), time.time()
        recap = self.today_recap(now) if isinstance(power, LaptopPower) else None
        return sayings_for(power, self.preferences.low_threshold, recap)

    def pet_host(self):
        return PetHost(
            photos=self.photos, images=self.companion_images,
            position=PetPosition(enabled=not (self.preview or self.smoke)),
            preferences=lambda: self.store.preferences,
            thought=lambda: thought_for(self.current_power(), self.preferences.low_threshold),
            sayings=self.pet_sayings, update=self.update_preferences,
            open_power=lambda: self.open_page(None, POWER_PAGE),
            open_settings=lambda: self.open_settings(PET_SECTION))

    def sync_pet(self):
        """依設定建立、調整或關閉桌面寵物。"""
        prefs = self.preferences
        if not prefs.desktop_pet:
            self.close_pet()
            return
        if self.pet is None:
            self.pet = self.pet_factory(self.pet_host())
            self.apply_companion_settings()
            self.update_companions()
            self.pet.show()
            self.update_companion_motion()
        self.pet.apply(prefs.desktop_pet_on_wallpaper, prefs.desktop_pet_scale)

    def photo_changed(self):
        """換了或刪除我的照片：重畫夥伴與桌面寵物（寵物的輸入範圍也跟著圖案）。"""
        for view in self.companion_views():
            view.queue_draw()
        if self.pet is not None:
            self.pet.update_shape()

    def companion_context(self):
        return CompanionContext(
            photos=self.photos, images=self.companion_images, photo_changed=self.photo_changed,
            icon_problem=lambda: self.icon_problem, saving=lambda: self.cadence.saving,
            x11=is_x11(self.window.get_display()))

    def close_pet(self):
        if self.pet is not None:
            pet, self.pet = self.pet, None
            pet.destroy()

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
        if self.pet is not None:
            self.pet.status_changed()

    def update_companion_motion(self):
        if self.companion is not None:
            visible = self.window_shown() and not self.window_obscured
            self.companion.set_motion(visible, self.cadence.saving)
        if self.pet is not None:
            self.pet.set_motion(self.cadence.saving)

    def companion_settings_changed(self, changed):
        if changed & PET_KEYS:
            self.sync_pet()
        if changed & FIGURE_KEYS:
            self.apply_companion_settings()
        if changed & (FIGURE_KEYS | STATE_KEYS):
            self.update_companions()
