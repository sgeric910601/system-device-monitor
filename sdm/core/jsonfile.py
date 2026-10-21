"""JSON 檔案讀取、原子寫入、不覆寫的備份，以及本程式殘留暫存檔的清理。"""
import json
import logging
import os
import re
import secrets
import stat
import time

from sdm.constants import Storage

LOG = logging.getLogger(__name__)
TOKEN_BYTES = 8           # 暫存檔名中的亂數：16 位小寫十六進位
TEMPORARY_SUFFIX = ".tmp"


def load_json(path):
    """讀取 JSON 物件；讀不到、格式錯誤或不是物件時回傳空字典。"""
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def json_text(value):
    """設定檔共用的文字格式：保留中文、縮排 2 格、結尾換行。"""
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def temporary_pattern(path):
    """本程式為 path 建立的暫存檔名：.<檔名>.<16 位十六進位>.tmp。"""
    return re.compile(re.escape("." + path.name + ".") + "[0-9a-f]{%d}" % (TOKEN_BYTES * 2)
                      + re.escape(TEMPORARY_SUFFIX))


def write_exclusive(path, data):
    """以獨占方式建立新檔並寫入、fsync。

    檔案已存在時拋出 FileExistsError，絕不覆寫；建立後任何失敗都會關閉描述子、
    移除這個剛建立的檔案並重新拋出。權限為 0666 扣除 umask，與一般寫檔相同。
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def write_json_atomic(path, value):
    """先寫同目錄暫存檔並 fsync，再以 os.replace 取代；失敗時原檔不變、不留暫存檔。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(TOKEN_BYTES)}{TEMPORARY_SUFFIX}")
    write_exclusive(temporary, json_text(value).encode("utf-8"))
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def create_backup(path, data, epoch):
    """把 data 存成同目錄的新備份檔並回傳其路徑。

    檔名為 <主檔名>-<epoch><副檔名>.bak；已存在時依序加 -1、-2…，絕不覆寫。
    """
    for attempt in range(Storage.BACKUP_NAME_ATTEMPTS):
        number = f"-{attempt}" if attempt else ""
        backup = path.with_name(f"{path.stem}-{epoch}{number}{path.suffix}.bak")
        try:
            write_exclusive(backup, data)
        except FileExistsError:
            continue
        _sync_directory(path.parent)
        return backup
    raise FileExistsError("No free backup name for " + path.name)


def remove_stale_temporaries(path, max_age=Storage.STALE_TEMPORARY_SECONDS, now=time.time):
    """移除本程式先前替 path 留下且超過 max_age 秒的暫存檔。

    只處理完全符合 temporary_pattern() 的一般檔案；符號連結、目錄及其他檔名一律不碰。
    回傳已移除的路徑。
    """
    pattern = temporary_pattern(path)
    try:
        entries = sorted(path.parent.iterdir())
    except FileNotFoundError:
        return ()
    except OSError as error:
        LOG.warning("Cannot scan for stale temporary files: %s", type(error).__name__)
        return ()
    limit = now() - max_age
    removed = []
    for entry in entries:
        if pattern.fullmatch(entry.name) and _remove_if_stale(entry, limit):
            removed.append(entry)
    return tuple(removed)


def _remove_if_stale(entry, limit):
    try:
        info = entry.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mtime >= limit:
            return False
        entry.unlink()
        return True
    except OSError as error:
        LOG.warning("Cannot remove stale temporary file: %s", type(error).__name__)
        return False


def _sync_directory(directory):
    """盡力把目錄項目的變更寫入磁碟；檔案系統不支援時略過。"""
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
