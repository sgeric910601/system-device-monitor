"""共用的唯讀來源輔助：讀取小型文字檔與執行外部命令。"""
import os
from pathlib import Path
import subprocess
import unicodedata

from sdm.constants import Collect


def read(path, default=""):
    """讀取並去除前後空白；讀不到或無法解碼時回傳 default。"""
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return default


def command(args, timeout=Collect.COMMAND_TIMEOUT):
    """以 C 語系執行命令並回傳標準輸出；失敗或逾時會拋出例外。"""
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C"}, check=True,
                            stdin=subprocess.DEVNULL)
    return result.stdout


def clean_text(value, limit):
    """把控制字元換成空白、合併連續空白，最多保留 limit 個字元。"""
    printable = "".join(" " if unicodedata.category(char).startswith("C") else char
                        for char in str(value))
    return " ".join(printable.split())[:limit]
