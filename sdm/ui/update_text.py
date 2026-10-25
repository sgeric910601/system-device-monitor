"""設定 → 更新的文字（不載入 GTK）與更新通知的內容。

更新內容摘要只顯示純文字（去掉控制字元、限制長度，以 Gtk.Label.set_text 顯示，不解讀標記）。
"""
import shlex
import time

from sdm.core.alert_messages import Alert
from sdm.core.update_logic import notes_excerpt
from sdm.services import system_components
from sdm.services.privileged import KIND_LOCAL
from sdm.services.updater import (AVAILABLE, CHECKING, DOWNLOADING, FAILED, IDLE, INSTALLING,
                                  KIND_DEB, KIND_SOURCE, LATEST, RESTARTING)

SETTINGS_PAGE = "settings:update"       # 點通知時開啟「設定 → 更新」
UPDATE_ALERT = "update_available"
ALERT_ICON = "software-update-available-symbolic"
KINDS = {KIND_DEB: ".deb 套件", KIND_SOURCE: "原始碼資料夾"}
RELEASE_PHASES = (AVAILABLE, DOWNLOADING, INSTALLING, RESTARTING)
DISABLED = "模擬測試不會檢查更新，也不會連線到網路。"
AUTO_TITLE = "自動檢查更新"
NOTIFY_TITLE = "有新版本時通知"
AUTO_NOTE = ("開啟後，看板啟動 15 秒後與之後每 6 小時看一次，但每 24 小時最多連線一次到 GitHub"
             "（api.github.com），只送出含版本號的 User-Agent，不送任何其他資料。"
             "自動檢查只看正式版，同一個版本只通知一次。")
CHECK = "立即檢查"
CHECK_NOTE = "「立即檢查」也會列出比較新的測試版（Beta）；測試版不會自動通知。"
INSTALL = "安裝更新"
INSTALL_NOTE = ("安裝前先在這台電腦上驗證發布簽章與檢查碼，再要求管理員密碼，由系統的 apt-get 安裝；"
                "完成後看板會自動重新啟動。")
BETA_NOTE = "這是測試版（Beta），可能不穩定。"
NO_KEYS = "未設定簽章金鑰：這個版本無法一鍵更新，請到 GitHub 下載新版。"
SOURCE_NOTE = "原始碼資料夾以 git 更新：在終端機執行下列指令，完成後從頂端列選單「退出」再重新開啟看板。"
HELPER_NOTE = "管理員元件有變更時（「設定 → 管理員功能」顯示需要重新安裝），再執行："
HELPER_MISMATCH = ("管理員元件的版本與目前的程式不符（例如 git pull 之後小程式有變更），"
                   "請在終端機執行：")
PAGE_LINK = "在 GitHub 查看這個版本"
NOTES_TITLE = "更新內容"
STATUS = {IDLE: "尚未檢查", CHECKING: "檢查中…", LATEST: "已是最新版本",
          INSTALLING: "等待管理員授權並安裝…", RESTARTING: "已安裝，正在重新啟動…"}


def version_line(current, kind):
    return f"目前版本 {current.text}（{KINDS[kind]}）"


def release_text(release):
    return release.version_text + ("（測試版）" if release.is_beta else "")


def status_text(state):
    if state.phase == FAILED:
        return state.error
    if state.phase == AVAILABLE and state.release is not None:
        return "有新版本 " + release_text(state.release)
    if state.phase == DOWNLOADING and state.release is not None:
        return f"正在下載並驗證 {state.release.version_text}…"
    return STATUS.get(state.phase, "")


def error_text(state):
    """安裝失敗等說明（檢查失敗已經寫在狀態裡）。"""
    return "" if state.phase == FAILED else state.error


def keys_text(kind, keys_configured):
    return NO_KEYS if kind == KIND_DEB and not keys_configured else ""


def last_check_text(stamp, localtime=time.localtime):
    if stamp is None:
        return "還沒有檢查過"
    return "上次檢查：" + time.strftime("%Y-%m-%d %H:%M", localtime(stamp))


def show_release(state):
    return state.phase in RELEASE_PHASES and state.release is not None


def notes_text(state):
    return notes_excerpt(state.release.notes) if show_release(state) else ""


def helper_mismatch(kind, admin_state):
    """原始碼資料夾安裝過管理員元件，但它回報的通訊協定與目前的程式不同。"""
    return kind == KIND_SOURCE and admin_state is not None and admin_state.kind == KIND_LOCAL \
        and admin_state.protocol is False


def git_command(root):
    return f"git -C {shlex.quote(str(root))} pull --ff-only"


def reinstall_command(root):
    return system_components.install_command(root)


def update_alert(release):
    return Alert(UPDATE_ALERT, f"有新版本：{release.version_text}",
                 "點一下開啟「設定 → 更新」查看更新內容與更新方式。", ALERT_ICON,
                 page=SETTINGS_PAGE)
