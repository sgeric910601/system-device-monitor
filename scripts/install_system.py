#!/usr/bin/python3
"""安裝或移除原始碼資料夾的管理員元件：CPU 功耗取樣與充電上限（由 install.py 經 pkexec 以 root 執行）。

用法：pkexec /usr/bin/python3 -I -B -c <啟動碼> <暫存>/scripts/install_system.py <本檔的 SHA-256>
      install｜remove --stage <暫存> --manifest-sha256 <64 位小寫十六進位>
  啟動碼（sdm/services/install_guard.py 的 BOOTSTRAP）先確認本檔的擁有者、所在目錄與雜湊，
  再執行剛才比對過的同一份位元組；這裡收到的參數從 install｜remove 開始。
- 必須以 root 執行且由 pkexec 設定 PKEXEC_UID；暫存目錄必須屬於該使用者且只有他能存取，
  它的每一層上層目錄都必須屬於 root 或該使用者、群組與其他人不可寫（root 擁有且有黏著位元的
  /tmp 除外）。暫存檔一律以 O_NOFOLLOW｜O_NONBLOCK 開啟（FIFO 等特殊檔案不會卡住）。
- install：manifest.json 的 SHA-256 必須等於參數；manifest 必須剛好列出下方 FILES 的來源；
  每個檔案以 O_NOFOLLOW 逐層開啟、讀進記憶體並比對雜湊，全部通過後才開始寫入。
  寫入位置與權限固定寫在這裡（/usr/local/libexec、/usr/local/share/polkit-1/actions、
  /etc/systemd/system），路徑上每一層都必須屬於 root 且群組與其他人不可寫，
  先寫暫存檔再整檔取代。之後重新載入 systemd；只有 socket 原本已啟用時才重新啟動它
  （充電上限的單元只在使用者選擇保留時由 sdm-charge-limit 啟用，這裡不啟用）。
  已安裝 .deb 套件時拒絕（兩者擇一）。
- remove：停用並停止 socket 與服務、停用充電上限的單元，只刪除 FILES 列出的檔案、drop-in、
  sockets.target.wants 與 multi-user／sleep.target.wants 的符號連結、原始碼版保留的
  充電上限狀態檔（/var/lib/system-device-monitor/charge-limit-local.json）與充電上限小程式的鎖檔、
  寫入時間（/run/system-device-monitor/charge-limit.lock、.times）；目錄裡還有其他檔案時保留目錄。
標準輸出只有一行 JSON；結束碼固定（0 成功、2 參數錯誤、3 失敗、4 沒有權限）。
只使用標準函式庫，不載入專案裡的任何模組。
"""
import hashlib
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
from typing import Callable, NamedTuple

EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_NOT_PERMITTED = 0, 2, 3, 4
ACTIONS = ("install", "remove")
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
MAX_FILE_BYTES = 1 << 20
DIGEST = re.compile(r"[0-9a-f]{64}")
UID_PATTERN = re.compile(r"[1-9][0-9]{0,9}")
MAX_UID = 4294967294
NOBODY = 65534
HELPER_DIR = "usr/local/libexec/system-device-monitor"
UNIT_DIR = "etc/systemd/system"
SOCKET = "system-device-monitor-local-cpu-power.socket"
SERVICE = "system-device-monitor-local-cpu-power.service"
LIMIT_UNIT = "system-device-monitor-local-charge-limit.service"
LIMIT_POLICY = "tw.local.system-device-monitor.unattended.local.charge-limit.policy"
POLICY_DIR = "usr/local/share/polkit-1/actions"
FILES = (           # （暫存目錄中的來源、相對於根目錄的位置、權限）
    ("helper/sdm-cpu-power", HELPER_DIR + "/sdm-cpu-power", 0o755),
    ("helper/sdm-cpu-power-service", HELPER_DIR + "/sdm-cpu-power-service", 0o755),
    ("helper/policy/tw.local.system-device-monitor.local.policy",
     POLICY_DIR + "/tw.local.system-device-monitor.local.policy", 0o644),
    ("helper/systemd/" + SOCKET, UNIT_DIR + "/" + SOCKET, 0o644),
    ("helper/systemd/" + SERVICE, UNIT_DIR + "/" + SERVICE, 0o644),
    ("helper/sdm-charge-limit", HELPER_DIR + "/sdm-charge-limit", 0o755),
    ("helper/policy/" + LIMIT_POLICY, POLICY_DIR + "/" + LIMIT_POLICY, 0o644),
    ("helper/systemd/" + LIMIT_UNIT, UNIT_DIR + "/" + LIMIT_UNIT, 0o644),
)
DROPIN_DIR = UNIT_DIR + "/" + SOCKET + ".d"
DROPIN_NAME = "owner.conf"
WANTS_DIR = UNIT_DIR + "/sockets.target.wants"
LIMIT_WANTS = tuple(f"{UNIT_DIR}/{target}.wants/{LIMIT_UNIT}"
                    for target in ("multi-user.target", "sleep.target"))
STATE_DIR = "var/lib/system-device-monitor"
LIMIT_STATE = STATE_DIR + "/charge-limit-local.json"     # 只刪除原始碼版自己的狀態檔
RUN_DIR = "run/system-device-monitor"
LIMIT_LOCK = RUN_DIR + "/charge-limit.lock"     # sdm-charge-limit 的鎖檔（執行期，重開機就消失）
LIMIT_TIMES = RUN_DIR + "/charge-limit.times"   # 各電池上次寫入的時間（執行期）
PACKAGE = "system-device-monitor"
NOT_INSTALLED = ("not-installed", "config-files")
SYSTEMCTL = "/usr/bin/systemctl"
DPKG_QUERY = "/usr/bin/dpkg-query"
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"}
COMMAND_TIMEOUT_S = 60
DIRECTORY_MODE = 0o755
UNSAFE_BITS = stat.S_IWGRP | stat.S_IWOTH
PRIVATE_BITS = 0o077
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class Usage(Exception):
    """參數不符合固定的用法。"""


class NotPermitted(Exception):
    """不是由 pkexec 以 root 執行。"""


class Failed(Exception):
    """無法安全地完成；訊息為固定的英文代號（也是輸出的 error）。"""


class Context(NamedTuple):
    """執行環境；測試以假根目錄、假的擁有者與假的命令代替。"""
    root: str
    owner: int
    run: Callable           # argv →（結束碼、標準輸出）
    euid: int
    user_exists: Callable
    lstat: Callable = os.lstat


def run_command(argv):
    """只以參數清單執行（不經過 shell），固定的環境變數。"""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                env=SAFE_ENV, timeout=COMMAND_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return result.returncode, result.stdout


def user_exists(uid):
    try:
        pwd.getpwuid(uid)
    except KeyError:
        return False
    return True


def parse_args(argv):
    if len(argv) != 5 or argv[0] not in ACTIONS or argv[1] != "--stage" \
            or argv[3] != "--manifest-sha256":
        raise Usage()
    stage, digest = argv[2], argv[4]
    if not stage.startswith("/") or os.path.normpath(stage) != stage or "\0" in stage \
            or not DIGEST.fullmatch(digest):
        raise Usage()
    return argv[0], stage, digest


def caller_uid(env, context):
    if context.euid != 0:
        raise NotPermitted()
    text = env.get("PKEXEC_UID", "")
    if not UID_PATTERN.fullmatch(text):
        raise NotPermitted()
    uid = int(text)
    if uid in (0, NOBODY) or uid > MAX_UID or not context.user_exists(uid):
        raise NotPermitted()
    return uid


# ── 讀取並驗證暫存目錄 ─────────────────────────────────────

def read_at(dir_fd, relative):
    """逐層開啟（每一層都不跟隨符號連結），讀出一般檔案的內容。"""
    parts = relative.split("/")
    fd = os.dup(dir_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        handle = os.open(parts[-1], FILE_FLAGS, dir_fd=fd)
    finally:
        os.close(fd)
    with os.fdopen(handle, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise Failed("invalid-stage")
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise Failed("invalid-stage")
    return data


def parse_manifest(data):
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise Failed("invalid-manifest") from None
    if not isinstance(manifest, dict):
        raise Failed("invalid-manifest")
    files = manifest.get("files")
    if manifest.get("format") != MANIFEST_FORMAT or not isinstance(files, dict) \
            or set(files) != {source for source, _, _ in FILES} \
            or not all(isinstance(value, str) and DIGEST.fullmatch(value)
                       for value in files.values()):
        raise Failed("invalid-manifest")
    return files


def safe_parent(info, uid):
    """上層目錄：屬於 root 或呼叫者、群組與其他人不可寫；root 擁有且有黏著位元時例外。"""
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid):
        return False
    return not info.st_mode & UNSAFE_BITS or (info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX))


def check_stage_parents(stage, uid, lstat):
    parts = stage.split("/")[1:-1]
    for end in range(len(parts) + 1):
        try:
            info = lstat("/" + "/".join(parts[:end]))
        except OSError:
            raise Failed("invalid-stage") from None
        if not safe_parent(info, uid):
            raise Failed("invalid-stage")


def read_stage(stage, digest, uid, lstat=os.lstat):
    """暫存目錄必須屬於呼叫者、只有他能存取、上層都安全；回傳 來源 → 已驗證雜湊的內容。"""
    check_stage_parents(stage, uid, lstat)
    try:
        stage_fd = os.open(stage, DIRECTORY_FLAGS)
    except OSError:
        raise Failed("invalid-stage") from None
    try:
        info = os.fstat(stage_fd)
        if info.st_uid != uid or info.st_mode & PRIVATE_BITS:
            raise Failed("invalid-stage")
        manifest = read_at(stage_fd, MANIFEST_NAME)
        if hashlib.sha256(manifest).hexdigest() != digest:
            raise Failed("manifest-mismatch")
        expected = parse_manifest(manifest)
        contents = {}
        for source, _, _ in FILES:
            data = read_at(stage_fd, source)
            if hashlib.sha256(data).hexdigest() != expected[source]:
                raise Failed("file-mismatch")
            contents[source] = data
        return contents
    except OSError:
        raise Failed("invalid-stage") from None
    finally:
        os.close(stage_fd)


# ── 寫入與刪除 ─────────────────────────────────────────────

def check_directory(fd, owner):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & UNSAFE_BITS:
        raise Failed("untrusted-directory")


def make_directory(parent, name):
    """建立目錄並回傳 True；已存在時回傳 False（不改變既有目錄的權限）。"""
    try:
        os.mkdir(name, DIRECTORY_MODE, dir_fd=parent)
    except FileExistsError:
        return False
    return True


def open_directory(context, relative, create=False):
    """從根目錄逐層開啟（不跟隨符號連結），每一層都必須受信任；create 時建立缺少的層，
    新建的目錄一律 0755（不受 umask 影響，polkitd 與一般使用者才讀得到）。"""
    fd = os.open(context.root, DIRECTORY_FLAGS)
    try:
        check_directory(fd, context.owner)
        for part in relative.split("/"):
            created = create and make_directory(fd, part)
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            if created:
                os.fchmod(fd, DIRECTORY_MODE)
            check_directory(fd, context.owner)
    except BaseException:
        os.close(fd)
        raise
    return fd


def replace_file(dir_fd, name, data, mode):
    """先寫同目錄的暫存檔（O_EXCL、O_NOFOLLOW）並 fsync，再整檔取代；只取代一般檔案。"""
    try:
        existing = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise Failed("unexpected-file")
    temporary = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 mode, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    os.fsync(dir_fd)


def remove_entry(context, relative, allowed=(stat.S_IFREG, stat.S_IFLNK)):
    """刪除一個檔案或符號連結；上層不存在時略過，其他類型保留不動。"""
    parent, name = relative.rsplit("/", 1)
    try:
        fd = open_directory(context, parent)
    except FileNotFoundError:
        return
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_IFMT(info.st_mode) in allowed:
            os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(fd)


def remove_empty_directory(context, relative):
    parent, name = relative.rsplit("/", 1)
    try:
        fd = open_directory(context, parent)
    except FileNotFoundError:
        return
    try:
        os.rmdir(name, dir_fd=fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ── 動作 ───────────────────────────────────────────────────

def deb_installed(context):
    code, output = context.run([DPKG_QUERY, "-W", "-f=${Status}", PACKAGE])
    words = output.split()
    return code == 0 and len(words) == 3 and words[2] not in NOT_INSTALLED


def systemctl(context, *args):
    return context.run([SYSTEMCTL, *args])[0]


def install(context, stage, digest, uid):
    if deb_installed(context):
        raise Failed("deb-installed")
    contents = read_stage(stage, digest, uid, context.lstat)
    for source, target, mode in FILES:
        parent, name = target.rsplit("/", 1)
        fd = open_directory(context, parent, create=True)
        try:
            replace_file(fd, name, contents[source], mode)
        finally:
            os.close(fd)
    systemctl(context, "daemon-reload")
    if systemctl(context, "is-enabled", "--quiet", SOCKET) == 0:
        systemctl(context, "stop", SERVICE)
        systemctl(context, "restart", SOCKET)
    return {"ok": True, "action": "install"}


def remove(context, stage, digest, uid):
    read_stage(stage, digest, uid, context.lstat)
    systemctl(context, "disable", "--now", SOCKET)      # 尚未安裝或已移除時失敗也沒關係
    systemctl(context, "stop", SERVICE)
    systemctl(context, "disable", LIMIT_UNIT)
    for _, target, _ in FILES:
        remove_entry(context, target)
    remove_entry(context, DROPIN_DIR + "/" + DROPIN_NAME)
    remove_empty_directory(context, DROPIN_DIR)
    for link in (WANTS_DIR + "/" + SOCKET, *LIMIT_WANTS):
        remove_entry(context, link, allowed=(stat.S_IFLNK,))
    remove_entry(context, LIMIT_STATE)
    remove_empty_directory(context, STATE_DIR)
    remove_entry(context, LIMIT_LOCK)
    remove_entry(context, LIMIT_TIMES)
    remove_empty_directory(context, RUN_DIR)
    remove_empty_directory(context, HELPER_DIR)
    systemctl(context, "daemon-reload")
    return {"ok": True, "action": "remove"}


def emit_json(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def production_context():
    return Context(root="/", owner=0, run=run_command, euid=os.geteuid(),
                   user_exists=user_exists)


def main(argv, env, context=None, emit=emit_json):
    try:
        action, stage, digest = parse_args(argv)
    except Usage:
        emit({"ok": False, "error": "usage"})
        return EXIT_USAGE
    context = context or production_context()
    try:
        uid = caller_uid(env, context)
        result = (install if action == "install" else remove)(context, stage, digest, uid)
    except NotPermitted:
        emit({"ok": False, "error": "not-permitted"})
        return EXIT_NOT_PERMITTED
    except Failed as error:
        emit({"ok": False, "error": str(error)})
        return EXIT_FAILED
    except (OSError, ValueError, RecursionError):
        emit({"ok": False, "error": "failed"})
        return EXIT_FAILED
    emit(result)
    return EXIT_OK


if __name__ == "__main__":
    ENVIRONMENT = dict(os.environ)
    os.environ.clear()
    os.environ.update(SAFE_ENV)
    sys.exit(main(sys.argv[1:], ENVIRONMENT))
