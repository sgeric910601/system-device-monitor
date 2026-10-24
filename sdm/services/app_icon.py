"""程式圖示的顏色（preferences.app_icon_color）；不依賴 GUI。

- 桌面入口一律用圖示名稱 system-device-monitor。.deb 把預設的薄荷綠安裝到
  /usr/share/icons/hicolor 的 128 與 512 px；選其他顏色時，把該顏色的兩張圖寫進使用者的
  圖示主題（XDG_DATA_HOME/icons/hicolor/…/apps/system-device-monitor.png），會優先於系統的那張。
- 原始碼安裝沒有系統圖示：install.py 寫入目前選的顏色（包括薄荷綠），之後換色時覆寫。
- 寫入的檔案與內容雜湊記在 XDG 資料目錄的 app-icon.json，而且每個圖示寫入之前先記下，
  寫到一半失敗時也一定列在記錄中。只覆寫或刪除記錄中、內容沒有被改過、而且位置正是上面兩個
  路徑的一般檔案；其他檔案（包括同名但不是本程式寫的）一律不動。
- 兩個尺寸只要有一個不能寫（別人的檔案、符號連結，或資料夾是符號連結），兩個都不寫，
  讓兩個尺寸維持同一個顏色。絕不跟隨或刪除符號連結。
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


def _hashes(value):
    """記錄中的一個檔案 → 可能的內容雜湊（寫入前先記下，所以可能同時有新舊兩個）。"""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _owned(data_home, data_dir):
    """記錄中、位置正確的檔案 → 雜湊；記錄被竄改成其他位置時忽略。"""
    allowed = {str(path) for path in theme_targets(data_home).values()}
    files = load_json(Path(data_dir)/AppIcon.MARKER).get(FILES)
    if not isinstance(files, dict):
        return {}
    return {name: _hashes(value) for name, value in files.items()
            if name in allowed and _hashes(value)}


def _safe_location(path, data_home):
    """圖示主題中這個位置與它的每一層資料夾（icons 以下）都不是符號連結。"""
    base, current = Path(data_home)/"icons", path
    while True:
        if current.is_symlink():
            return False
        if current == base or current == current.parent:
            return True
        current = current.parent


def _content(path):
    """一般檔案的內容雜湊；不存在、是符號連結或讀不到時為 None。"""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return _digest(path.read_bytes())
    except OSError:
        return None


def _ours(path, owned):
    actual = _content(path)
    return actual is not None and actual in owned.get(str(path), ())


def _writable(path, owned, data_home):
    """可以寫：位置安全，而且還沒有檔案或是本程式寫的檔案。"""
    if not _safe_location(path, data_home):
        return False
    return not (path.exists() or path.is_symlink()) or _ours(path, owned)


def _settled(recorded):
    """寫完（或失敗）之後：每個檔案只留目前內容的雜湊；已經不在或不是本程式內容的移除。"""
    settled = {}
    for name, hashes in recorded.items():
        actual = _content(Path(name))
        if actual is not None and actual in hashes:
            settled[name] = [actual]
    return settled


def _save_marker(marker, color, files, record):
    if files:
        record(marker, {COLOR: color, FILES: files})
    else:
        marker.unlink(missing_ok=True)


def _write_pending(pending, marker, color, recorded, writer, record):
    """每個圖示寫入之前先把預定的雜湊記進記錄，寫入失敗時之後仍認得它。"""
    changed = False
    try:
        for path, data in pending.items():
            hashes = recorded.setdefault(str(path), [])
            if _digest(data) not in hashes:
                hashes.append(_digest(data))
            record(marker, {COLOR: color, FILES: recorded})
            writer(path, data)
            changed = True
    finally:
        try:
            _save_marker(marker, color, _settled(recorded), record)
        except OSError as error:
            LOG.warning("Cannot tidy the application icon record: %s", type(error).__name__)
    return changed


def write_overrides(color, data_home, data_dir, root=None, writer=write_bytes_atomic,
                    record=write_json_atomic):
    """兩個尺寸都寫成 color；任何一個位置不能寫（別人的檔案、符號連結）時兩個都不寫。"""
    owned = _owned(data_home, data_dir)
    plan = {path: asset_path(color, size, root).read_bytes()
            for size, path in theme_targets(data_home).items()}
    if not all(_writable(path, owned, data_home) for path in plan):
        return IconResult(False, FOREIGN)
    pending = {path: data for path, data in plan.items() if _content(path) != _digest(data)}
    if not pending:
        return IconResult(False, None)
    recorded = {name: list(hashes) for name, hashes in owned.items()}
    changed = False
    try:
        changed = _write_pending(pending, Path(data_dir)/AppIcon.MARKER, color, recorded,
                                 writer, record)
    except OSError as error:
        LOG.warning("Cannot update the application icon: %s", type(error).__name__)
        return IconResult(any(_content(path) == _digest(data) for path, data in pending.items()),
                          WRITE_FAILED)
    return IconResult(changed, None)


def remove_overrides(data_home=None, data_dir=None, record=write_json_atomic):
    """刪除本程式寫入且沒有被改過的圖示（不刪符號連結）；回傳是否刪除了任何檔案。"""
    data_home, data_dir = data_home or paths.DATA_HOME, data_dir or paths.DATA
    owned, removed, remaining = _owned(data_home, data_dir), False, {}
    for name, hashes in owned.items():
        path = Path(name)
        if not (_ours(path, owned) and _safe_location(path, data_home)):
            continue
        try:
            path.unlink()
            removed = True
        except OSError as error:
            LOG.warning("Cannot remove an application icon: %s", type(error).__name__)
            remaining[name] = list(hashes)
    _save_marker(Path(data_dir)/AppIcon.MARKER, AppIcon.DEFAULT, remaining, record)
    return removed


def apply_color(color, packaged=None, data_home=None, data_dir=None, root=None,
                keep_default=False, writer=write_bytes_atomic, record=write_json_atomic):
    """讓圖示主題與選擇的顏色一致。keep_default：原始碼安裝時也寫入預設的薄荷綠。
    writer、record 為寫入圖示與記錄的函式（測試可替換）。"""
    if color not in COLORS:
        raise ValueError("Unknown icon colour: " + str(color))
    packaged = is_packaged(root) if packaged is None else packaged
    data_home, data_dir = data_home or paths.DATA_HOME, data_dir or paths.DATA
    try:
        if color == AppIcon.DEFAULT and (packaged or not (
                keep_default or _owned(data_home, data_dir))):
            return IconResult(remove_overrides(data_home, data_dir, record), None)
        return write_overrides(color, data_home, data_dir, root, writer, record)
    except OSError as error:
        LOG.warning("Cannot update the application icon: %s", type(error).__name__)
        return IconResult(False, WRITE_FAILED)
