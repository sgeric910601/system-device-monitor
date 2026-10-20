"""JSON 檔案讀取與原子寫入。"""
import json
import os
import secrets


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


def write_json_atomic(path, value):
    """先寫同目錄暫存檔並 fsync，再以 os.replace 取代。

    成功時不留下暫存檔；任何失敗都會移除暫存檔並重新拋出例外，原檔維持不變。
    暫存檔以 0666 建立，實際權限由 umask 決定，與一般寫檔相同。
    """
    text = json_text(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with open(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def _sync_directory(directory):
    """盡力把目錄項目的變更寫入磁碟；不支援時略過。"""
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
