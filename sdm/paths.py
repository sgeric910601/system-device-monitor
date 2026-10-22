"""安裝位置與 XDG 使用者路徑；不依賴 GUI。

其他模組請以 `from sdm import paths` 後讀取 `paths.CONFIG` 等屬性，
讓測試只需替換這裡的值即可把所有讀寫導向暫存目錄。
"""
import os
from pathlib import Path

APP_NAME = "system-device-monitor"

# 專案根目錄：sdm/ 的上一層（原始碼 checkout 或 /usr/share/system-device-monitor）。
ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text().strip()


def _xdg(variable, fallback):
    """依 XDG 規範取得基準目錄；環境變數未設定或為空時使用預設位置。"""
    return Path(os.environ.get(variable) or Path.home() / fallback)


CONFIG_HOME = _xdg("XDG_CONFIG_HOME", ".config")
DATA_HOME = _xdg("XDG_DATA_HOME", ".local/share")
CACHE_HOME = _xdg("XDG_CACHE_HOME", ".cache")
STATE_HOME = _xdg("XDG_STATE_HOME", ".local/state")

# 舊版設定檔：只存五個頂端列布林值，舊版與退回舊版時都會讀寫它。
CONFIG = CONFIG_HOME / APP_NAME / "settings.json"
# 新版設定檔：與 settings.json 同目錄，舊版不會讀取或覆寫。
PREFERENCES = CONFIG.parent / "preferences.json"
ALIASES = CONFIG.parent / "device-aliases.json"
AUTOSTART = CONFIG_HOME / "autostart" / (APP_NAME + ".desktop")
CACHE = CACHE_HOME / APP_NAME
# 頂端列的動態圖示：放在執行期目錄（登出就清掉）；沒有時改放快取目錄。
_RUNTIME = os.environ.get("XDG_RUNTIME_DIR")
ICONS = (Path(_RUNTIME) / APP_NAME / "icons") if _RUNTIME else CACHE / "icons"
DATA = DATA_HOME / APP_NAME
STATE = STATE_HOME / APP_NAME
# 視窗狀態（最後顯示的頁面），不是設定。
STATE_FILE = STATE / "state.json"
