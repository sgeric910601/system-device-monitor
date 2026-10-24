"""設定 →「夥伴」「桌面寵物」「外觀」的文字與照片動作（不載入 GTK，方便測試）。"""
from sdm.constants import Companion
from sdm.core.companion import HEADSHOT, PHOTO
from sdm.core.pet_geometry import size_presets

SCALES = size_presets()
PLACES = ((False, "浮在視窗上"), (True, "放在桌布上（其他視窗會蓋住牠）"))
CYCLE_NOTE = "在「電源」頁點一下夥伴也能換外觀，按右鍵可以直接選。"
LIVE_EYES = "貓咪大頭照的眼睛會眨眼"
FOLLOW = "貓咪大頭照的眼睛跟著滑鼠游標"
PET_SWITCH = "在桌面上顯示充電夥伴"
PET_HOWTO = "拖曳可以移動，滑鼠移過去看電量，點一下聽牠說話，左右來回摸牠會冒愛心，連點兩下打開看板，按右鍵有更多選項。"
WAYLAND = "目前是 Wayland 工作階段：桌面寵物會是一般的小視窗，不能放在桌布上或固定在最上層，也不會記住位置。"
PHOTO_PRIVACY = ("照片會從中間裁成正方形、縮成 512 × 512，重新存成不含拍攝時間、地點等資訊的圖檔，"
                 "只放在這台電腦的資料夾，不會上傳。")
PREVIEW_PHOTO = "示範模式不使用自己的照片，截圖只會出現內建的貓咪大頭照。"
CHOOSE = "選擇照片…"
BUSY = "處理中…"
REMOVE = "移除照片"
CHOOSER_TITLE = "選擇夥伴的照片"
CHOOSER_ACCEPT = "使用這張照片"
ICON_RELOGIN = "GNOME 的 Dock 與應用程式清單可能要重新登入後才會換成新的圖示。"


def animation_note(saving):
    if saving:
        return "省電模式生效中：夥伴與桌面寵物維持靜態圖，不播放動畫。"
    return (f"夥伴只在看得到時播放動畫（每秒最多 {Companion.MAX_FPS} 格）；"
            "省電模式生效時改成靜態圖。")


def photo_note(photos):
    return PHOTO_PRIVACY if photos.enabled else PREVIEW_PHOTO


def pet_note(x11):
    return PET_HOWTO if x11 else PET_HOWTO + WAYLAND


def icon_note(problem):
    return ICON_RELOGIN if not problem else problem + "。" + ICON_RELOGIN


def choose_photo(photos, path, apply, changed, finished):
    """在背景處理照片；存好後（回到主執行緒）改用「我的照片」。
    finished 收到要顯示的原因（照片不能用時，設定不變）或 None。"""
    def done(message):
        if message is None:
            changed()
            apply(companion_appearance=PHOTO)
        finished(message)
    photos.save_async(path, done)


def remove_photo(photos, appearance, apply, changed):
    """刪掉照片；正在用「我的照片」時改回貓咪大頭照。"""
    photos.remove()
    changed()
    if appearance == PHOTO:
        apply(companion_appearance=HEADSHOT)
