"""一鍵更新的下載與驗證（在工作執行緒執行，不碰 GTK）。

順序：XDG_RUNTIME_DIR 底下新的私人暫存目錄（0700）→ 下載 SHA256SUMS 與 SHA256SUMS.sig →
先以內建公鑰驗證簽章（沒有金鑰時完全不連線）→ SHA256SUMS 中這個 .deb 檔名的雜湊 → 下載 .deb
（≤ 30 MB，邊下載邊算 SHA-256）並比對 → dpkg-deb -f（Package、Architecture all、Version 與 Release
相同）→ dpkg --compare-versions 比已安裝的版本新 → 更新元件（sdm-update）的通訊協定相符。
任何一步失敗都拋出 UpdateError（訊息給使用者看）；暫存目錄由呼叫的地方刪除。
root 端的 sdm-update 會再對自己的複本做一次同樣的檢查。
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, NamedTuple

from sdm.constants import Update
from sdm.core.update_logic import checksum
from sdm.data.github import BINARY, FetchError
from sdm.services import update_verify
from sdm.services.install_guard import private_directory
from sdm.services.privileged import trusted_path

NO_KEYS = "此版本未設定更新簽章金鑰，無法一鍵更新；請到 GitHub 下載新版。"
UNSIGNED = "這個版本沒有附上簽章，無法一鍵更新；請到 GitHub 下載。"
NO_RUNTIME = "找不到私人的暫存目錄（XDG_RUNTIME_DIR），無法下載更新。"
TOO_LARGE = "新版的檔案大小超過上限。"
BAD_SIGNATURE = "簽章驗證失敗，已停止更新。"
NOT_LISTED = "簽章過的 SHA256SUMS 裡沒有這個 .deb，已停止更新。"
MISMATCH = "下載的 .deb 與簽章過的檢查碼不符，已停止更新。"
WRONG_PACKAGE = "下載的套件與這個程式或版本不符，已停止更新。"
NOT_NEWER = "下載的版本沒有比已安裝的版本新。"
REINSTALL = "更新元件（sdm-update）找不到或版本不符，請重新安裝 .deb 套件。"
SAVE = "無法寫入暫存檔。"
FIELD = re.compile(r"(Package|Architecture|Version): (\S+)", re.ASCII)
DEBIAN_VERSION = re.compile(r"[0-9][A-Za-z0-9.+~:-]{0,63}", re.ASCII)
INSTALLED = "install ok installed"
SAFE_ENV = {"PATH": Update.SAFE_PATH, "LANG": "C"}
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
PRIVATE_FILE = 0o600
PRIVATE_DIRECTORY = 0o700


class UpdateError(Exception):
    """下載或驗證失敗；訊息給使用者看。"""


class Tools(NamedTuple):
    """外部檢查（測試注入）。"""
    verify: Callable            # verify(SHA256SUMS, 簽章內容, 公鑰, 暫存目錄) → bool
    fields: Callable            # fields(.deb) → 欄位字典或 None
    newer: Callable             # newer(新版本, 已安裝的版本) → bool
    helper_protocol: Callable   # helper_protocol() → 整數或 None


class Prepared(NamedTuple):
    directory: Path
    deb: Path
    sums: Path
    signature: Path
    version: str                # Debian 版本


def run_tool(argv, timeout=Update.COMMAND_TIMEOUT):
    """（結束碼、標準輸出）；無法執行或逾時時為（-1, b""）。只以參數清單執行。"""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                env=SAFE_ENV, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, b""
    return result.returncode, result.stdout


def private_workdir(environ, uid):
    """XDG_RUNTIME_DIR（屬於自己、只有自己能存取、上層安全）底下新的 0700 目錄。"""
    runtime = environ.get("XDG_RUNTIME_DIR", "")
    if not runtime.startswith("/"):
        raise UpdateError(NO_RUNTIME)
    runtime = "/" + os.path.normpath(runtime).lstrip("/")   # sdm-update 只接受正規化的路徑
    if not private_directory(runtime, uid, os.lstat):
        raise UpdateError(NO_RUNTIME)
    try:
        path = Path(tempfile.mkdtemp(prefix="sdm-update-", dir=runtime))
        os.chmod(path, PRIVATE_DIRECTORY)
    except OSError:
        raise UpdateError(NO_RUNTIME) from None
    return path


def write_private(path, data):
    try:
        fd = os.open(path, CREATE, PRIVATE_FILE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except OSError:
        raise UpdateError(SAVE) from None


def parse_fields(data):
    fields = {}
    for line in data.decode("ascii", "replace").split("\n"):
        if not line:
            continue
        match = FIELD.fullmatch(line)
        if match is None or match.group(1) in fields:
            return None
        fields[match.group(1)] = match.group(2)
    return fields


def deb_fields(deb, run=run_tool):
    code, output = run([Update.DPKG_DEB, "-f", str(deb), "Package", "Architecture", "Version"])
    return parse_fields(output) if code == 0 else None


def dpkg_newer(candidate, installed, run=run_tool):
    if not DEBIAN_VERSION.fullmatch(candidate) or not DEBIAN_VERSION.fullmatch(installed):
        return False
    return run([Update.DPKG, "--compare-versions", candidate, "gt", installed])[0] == 0


def installed_version(fallback, run=run_tool):
    """以 .deb 安裝的版本；查不到時用 fallback（執行中的版本）。"""
    code, output = run([Update.DPKG_QUERY, "-W", "-f=${Status}\\n${Version}\\n", Update.PACKAGE])
    lines = output.decode("ascii", "replace").split("\n")
    if code == 0 and len(lines) >= 2 and lines[0] == INSTALLED \
            and DEBIAN_VERSION.fullmatch(lines[1]):
        return lines[1]
    return fallback


def helper_protocol(trusted=trusted_path, run=run_tool):
    """受信任的 sdm-update 回報的通訊協定；找不到或回應不對時為 None。"""
    if not trusted(Update.HELPER):
        return None
    code, output = run([Update.HELPER, "--protocol"])
    try:
        value = json.loads(output.decode("utf-8")) if code == 0 else None
    except (UnicodeDecodeError, ValueError):
        return None
    protocol = value.get("protocol") if isinstance(value, dict) else None
    return protocol if type(protocol) is int else None


TOOLS = Tools(verify=update_verify.verify, fields=deb_fields, newer=dpkg_newer,
              helper_protocol=helper_protocol)


def fetch_signed_sums(release, client, workdir, keys, tools):
    """下載並驗證 SHA256SUMS；回傳（內容、路徑、簽章路徑）。"""
    sums = client.get(release.checksum_url, Update.CHECKSUMS_MAX_BYTES, accept=BINARY)
    signature = client.get(release.signature_url, Update.SIGNATURE_MAX_BYTES, accept=BINARY)
    sums_path, signature_path = workdir/Update.CHECKSUMS, workdir/Update.SIGNATURE
    write_private(sums_path, sums)
    write_private(signature_path, signature)
    if not tools.verify(sums_path, signature, keys, workdir):
        raise UpdateError(BAD_SIGNATURE)
    return sums, sums_path, signature_path


def check_release(release, keys):
    if not update_verify.usable_keys(keys):
        raise UpdateError(NO_KEYS)
    if not release.signed:
        raise UpdateError(UNSIGNED)
    if release.asset_size > Update.DEB_MAX_BYTES:
        raise UpdateError(TOO_LARGE)


def prepare(release, client, workdir, keys, installed, tools=TOOLS):
    """下載並驗證；回傳要交給 sdm-update 的三個檔案。"""
    check_release(release, keys)
    try:
        sums, sums_path, signature_path = fetch_signed_sums(release, client, workdir, keys, tools)
        try:
            expected = checksum(sums.decode("utf-8"), release.asset_name)
        except UnicodeDecodeError:
            expected = None
        if expected is None:
            raise UpdateError(NOT_LISTED)
        deb = workdir/release.asset_name
        if client.download(release.asset_url, deb, Update.DEB_MAX_BYTES) != expected:
            raise UpdateError(MISMATCH)
    except FetchError as error:
        raise UpdateError(str(error)) from None
    version = release.debian_version
    if tools.fields(deb) != {"Package": Update.PACKAGE, "Architecture": "all", "Version": version}:
        raise UpdateError(WRONG_PACKAGE)
    if not tools.newer(version, installed):
        raise UpdateError(NOT_NEWER)
    if tools.helper_protocol() != Update.PROTOCOL:
        raise UpdateError(REINSTALL)
    return Prepared(workdir, deb, sums_path, signature_path, version)
