"""設定 → 桌面寵物：開關、浮在視窗上或放在桌布上、大小與眼睛跟著游標。

外觀與會動的眼睛在「夥伴」設定（電源頁的夥伴與桌面寵物共用）；眼睛跟著游標也同時用在兩者。
Wayland 不能放在桌布上，另外說明限制。
"""
from sdm.ui.monitor_widgets import card
from sdm.ui.settings import companion_text as text
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box

PET = "desktop_pet"
PLACE = "desktop_pet_on_wallpaper"
SCALE = "desktop_pet_scale"


def build(controls, context):
    store, page, x11 = controls.store, box(spacing=16), context.companion.x11

    def shown():
        return store.preferences.desktop_pet
    page.pack_start(card("桌面寵物", controls.switch(PET, text.PET_SWITCH),
                         wrapped(text.pet_note(x11), "muted")), False, False, 0)
    place = controls.radios(PLACE, text.PLACES)
    controls.enable_when(place, PLACE, when=lambda: shown() and x11)
    size = controls.choice(SCALE, "大小", text.SCALES)
    controls.enable_when(size, SCALE, when=shown)
    follow = controls.switch("desktop_pet_follow_cursor", text.FOLLOW)
    page.pack_start(card("位置與大小", place, size, follow), False, False, 0)
    return page
