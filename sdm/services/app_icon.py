"""程式圖示的顏色（preferences.app_icon_color）；不依賴 GUI。

- 桌面入口一律用圖示名稱 system-device-monitor。.deb 把預設的薄荷綠安裝到
  /usr/share/icons/hicolor 的 128 與 512 px；選其他顏色時，把該顏色的兩張圖寫進使用者的
  圖示主題（XDG_DATA_HOME/icons/hicolor/…/apps/system-device-monitor.png），會優先於系統的那張。
- 原始碼安裝沒有系統圖示：install.py 寫入目前選的顏色（包括薄荷綠），之後換色時覆寫。
- 寫入的檔案與內容雜湊記在 XDG 資料目錄的 app-icon.json。只覆寫或刪除記錄中、
  內容沒有被改過、而且位置正是上面兩個路徑的檔案；其他檔案（包括同名但不是本程式寫的）一律不動。
- 寫入失敗只回報原因，不拋出例外。GNOME 可能要重新登入才會更新已快取的圖示。
"""
import hashlib
import logging
from pathlib import Path
from typing import NamedTuple, Optional

from sdm import paths
from sdm.constants import AppIcon
from sdm.core.jsonfile import load_json, write_bytes_atomic, write_json_atomic
from sdm.services.autostart import PACKAGE_ROOT

LOG = logging.getLogger(__name__)
COLORS = AppIcon.COLORS
TITLES = {"mint": "薄荷綠", "peach": "暖杏色", "night": "深夜藍", "sky": "天空藍"}
FILES = "files"
COLOR = "color"
FOREIGN = "圖示主題中已有不是本程式寫入的同名圖示，沒有覆蓋"
WRITE_FAILED = "無法寫入圖示，視窗與頂端列仍會使用選擇的顏色"


class IconResult(NamedTuple):
    changed: bool                   # 這次有寫入或刪除檔案
    problem: Optional[str]          # 需要告訴使用者的問題；沒有時為 None


def is_packaged(root=None):
    return Path(root or paths.ROOT) == PACKAGE_ROOT


def asset_path(color, size, root=None):
    return Path(root or paths.ROOT)/"assets/app"/f"{color}-{size}.png"


def theme_targets(data_home):
    """尺寸 → 使用者圖示主題中的位置。"""
    base = Path(data_home)/"icons/hicolor"
    return {size: base/f"{size}x{size}"/"apps"/(AppIcon.NAME + ".png") for size in AppIcon.SIZES}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _owned(data_home, data_dir):
    """記錄中、位置正確的檔案 → 寫入時的雜湊；記錄被竄改成其他位置時忽略。"""
    allowed = {str(path) for path in theme_targets(data_home).values()}
    files = load_json(Path(data_dir)/AppIcon.MARKER).get(FILES)
    if not isinstance(files, dict):
        return {}
    return {name: value for name, value in files.items()
            if name in allowed and isinstance(value, str)}


def _ours(path, owned):
    try:
        return _digest(path.read_bytes()) == owned.get(str(path))
    except OSError:
        return False


def _write_one(path, data, owned):
    """回傳（是否寫入、問題）。已經是同樣內容時不寫；別人的檔案不覆寫。"""
    if path.exists():
        if _ours(path, owned) and path.read_bytes() == data:
            return False, None
        if not _ours(path, owned):
            return False, FOREIGN
    write_bytes_atomic(path, data)
    return True, None


def write_overrides(color, data_home, data_dir, root=None):
    owned, changed, problem = _owned(data_home, data_dir), False, None
    recorded = dict(owned)
    for size, path in theme_targets(data_home).items():
        data = asset_path(color, size, root).read_bytes()
        wrote, issue = _write_one(path, data, owned)
        changed, problem = changed or wrote, problem or issue
        if issue is None:
            recorded[str(path)] = _digest(data)
    if changed or recorded != owned:
        write_json_atomic(Path(data_dir)/AppIcon.MARKER, {COLOR: color, FILES: recorded})
    return IconResult(changed, problem)


def remove_overrides(data_home=None, data_dir=None):
    """刪除本程式寫入且沒有被改過的圖示；回傳是否刪除了任何檔案。"""
    data_home, data_dir = data_home or paths.DATA_HOME, data_dir or paths.DATA
    owned, removed = _owned(data_home, data_dir), False
    for name in owned:
        path = Path(name)
        if _ours(path, owned):
            path.unlink()
            removed = True
    (Path(data_dir)/AppIcon.MARKER).unlink(missing_ok=True)
    return removed


def apply_color(color, packaged=None, data_home=None, data_dir=None, root=None,
                keep_default=False):
    """讓圖示主題與選擇的顏色一致。keep_default：原始碼安裝時也寫入預設的薄荷綠。"""
    if color not in COLORS:
        raise ValueError("Unknown icon colour: " + str(color))
    packaged = is_packaged(root) if packaged is None else packaged
    data_home, data_dir = data_home or paths.DATA_HOME, data_dir or paths.DATA
    try:
        if color == AppIcon.DEFAULT and (packaged or not (
                keep_default or _owned(data_home, data_dir))):
            return IconResult(remove_overrides(data_home, data_dir), None)
        return write_overrides(color, data_home, data_dir, root)
    except OSError as error:
        LOG.warning("Cannot update the application icon: %s", type(error).__name__)
        return IconResult(False, WRITE_FAILED)
