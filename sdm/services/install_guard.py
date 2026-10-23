"""原始碼安裝的安全檢查（install.py --system-components 的使用者端）與 root 端的啟動碼。

- 原始碼資料夾、.git（worktree 時為檔案）、實際的 git 目錄與共用 git 目錄，以及它們的每一層
  上層目錄，都必須屬於 root 或使用者、其他人不可寫；群組可寫只允許 root 擁有且有黏著位元的
  目錄（/tmp），或使用者私人群組（與帳號同名、沒有其他成員、也不是任何其他帳號的主要群組，
  Ubuntu 預設 umask 002 的情況）。否則別的帳號可能改掉 git 取出的內容。
- git 目錄與共用 git 目錄裡的每一項（HEAD、config、packed-refs、refs、info、objects 等，
  hooks 除外：git 指令一律以 core.hooksPath=/dev/null 執行）都必須是同樣規則下的目錄或一般檔案；
  objects/info/alternates、http-alternates、info/grafts 與 replace refs 會改變取出的內容，一律拒絕。
- 暫存目錄放在 XDG_RUNTIME_DIR（屬於使用者、0700、上層安全）；沒有時才用 root 擁有且有
  黏著位元的 /tmp。執行 pkexec 前再確認暫存目錄是使用者的 0700 目錄、每一層上層都安全
  （暫存目錄不放寬私人群組）。
- BOOTSTRAP 是固定的短程式，以 pkexec /usr/bin/python3 -I -B -c BOOTSTRAP <腳本> <SHA-256> …
  執行：以 O_NOFOLLOW｜O_NONBLOCK 開啟腳本，確認它是 PKEXEC_UID 擁有的一般檔案、位在只有他
  能存取的目錄、上層目錄都安全，讀一次、比對 SHA-256，再執行剛才比對過的同一份位元組；
  root 從不以路徑讓 Python 開啟使用者可寫的腳本。
"""
import grp
import os
from pathlib import PurePosixPath
import pwd
import stat

UNSAFE_REPOSITORY = ("原始碼資料夾、它的 .git 或上層目錄可以被其他帳號寫入，為了安全不安裝；"
                     "請改用只有你（或 root）能寫入的資料夾。")
UNSAFE_STAGE = "暫存目錄或它的上層目錄不安全，已停止，沒有做任何變更。"
UNSAFE_GIT_DATA = ("git 目錄裡有其他帳號能寫入、不是一般檔案的項目，或有 alternates、grafts、"
                   "replace 等會改變內容的設定；為了安全不安裝。")
SKIPPED = ("hooks",)                    # 不使用 hooks（core.hooksPath=/dev/null）
REDIRECTIONS = ("objects/info/alternates", "objects/info/http-alternates", "info/grafts")
REPLACE_REFS = "refs/replace/"
PACKED_REFS = "packed-refs"
NO_PRIVATE_TEMP = ("找不到安全的暫存位置（屬於你的 XDG_RUNTIME_DIR，或 root 擁有、有黏著位元的 "
                   "/tmp），已停止。")
HEAD_TEXT = {"install": "將以管理員權限安裝這個提交的元件：{}",
             "remove": "將以管理員權限移除元件（使用這個提交的移除程式）：{}"}
HEAD_MAX_CHARS = 120
TMP = "/tmp"
OTHERS = 0o077

BOOTSTRAP = """import hashlib, json, os, stat, sys
LIMIT = 1 << 20


def fail(code):
    sys.stdout.write(json.dumps({"ok": False, "error": "bootstrap"}) + "\\n")
    raise SystemExit(code)


def safe(info, uid):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid):
        return False
    return not info.st_mode & 0o022 or (info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX))


def load(argv, env, lstat=os.lstat, fstat=os.fstat, opener=os.open, geteuid=os.geteuid):
    text = env.get("PKEXEC_UID", "")
    if geteuid() != 0 or not (text.isascii() and text.isdigit()) or text.startswith("0"):
        fail(4)
    if len(argv) < 3:
        fail(2)
    uid, path, digest = int(text), argv[1], argv[2]
    if not path.startswith("/") or os.path.normpath(path) != path or len(digest) != 64 \\
            or digest.strip("0123456789abcdef"):
        fail(2)
    parts = path.split("/")[1:-1]
    try:
        if not all(safe(lstat("/" + "/".join(parts[:end])), uid) for end in range(len(parts) + 1)):
            fail(3)
        folder = lstat(os.path.dirname(path))
        if folder.st_uid != uid or folder.st_mode & 0o077:
            fail(3)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        with os.fdopen(opener(path, flags), "rb") as handle:
            info = fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
                fail(3)
            data = handle.read(LIMIT + 1)
    except OSError:
        fail(3)
    if len(data) > LIMIT or hashlib.sha256(data).hexdigest() != digest:
        fail(3)
    return path, data


if __name__ == "__main__":
    PATH, DATA = load(sys.argv, os.environ)
    sys.argv = [PATH] + sys.argv[3:]
    exec(compile(DATA, PATH, "exec"), {"__name__": "__main__", "__file__": PATH})
"""


class ComponentError(Exception):
    """無法安全地準備安裝；訊息是給使用者看的說明。"""


def writable_by_others(info, private_gid):
    """除了擁有者（與 root 擁有、有黏著位元的情況）以外，還有誰能寫入。"""
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if info.st_mode & stat.S_IWOTH:
        return not sticky_root
    if info.st_mode & stat.S_IWGRP:
        return not (sticky_root or (private_gid is not None and info.st_gid == private_gid))
    return False


def safe_entry(info, uid, private_gid=None, allow_file=False):
    kind_ok = stat.S_ISDIR(info.st_mode) or (allow_file and stat.S_ISREG(info.st_mode))
    return kind_ok and info.st_uid in (0, uid) and not writable_by_others(info, private_gid)


def safe_chain(path, uid, lstat, private_gid=None, allow_file=False):
    """path 本身與每一層上層目錄都安全（不跟隨符號連結：符號連結一律不算目錄）。"""
    pure = PurePosixPath(path)
    if not pure.is_absolute() or os.path.normpath(str(pure)) != str(path):
        return False
    try:
        if not all(safe_entry(lstat(str(parent)), uid, private_gid)
                   for parent in reversed(pure.parents)):
            return False
        return safe_entry(lstat(str(pure)), uid, private_gid, allow_file)
    except OSError:
        return False


def user_private_gid(uid, getpwuid=pwd.getpwuid, getgrgid=grp.getgrgid, getpwall=pwd.getpwall):
    """使用者私人群組的 gid：主要群組與帳號同名、沒有其他成員，也不是任何其他帳號的
    主要群組；不是時為 None。"""
    try:
        user = getpwuid(uid)
        group = getgrgid(user.pw_gid)
    except KeyError:
        return None
    if group.gr_name != user.pw_name or group.gr_mem:
        return None
    if any(other.pw_gid == user.pw_gid and other.pw_uid != uid for other in getpwall()):
        return None
    return user.pw_gid


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def scan_git_dir(top, uid, private_gid, lstat, listdir, read=read_bytes):
    """top 底下每一項（hooks 除外）都必須是使用者或 root 擁有、別人不能寫的目錄或一般檔案，
    而且沒有 alternates、grafts 或 replace refs。不安全時拋出 ComponentError。"""
    pending = [""]
    while pending:
        relative = pending.pop()
        try:
            names = listdir(top + "/" + relative if relative else top)
        except OSError:
            raise ComponentError(UNSAFE_GIT_DATA) from None
        for name in names:
            child = relative + "/" + name if relative else name
            if child in SKIPPED:
                continue
            try:
                info = lstat(top + "/" + child)
            except OSError:
                raise ComponentError(UNSAFE_GIT_DATA) from None
            if child in REDIRECTIONS or child.startswith(REPLACE_REFS) \
                    or not safe_entry(info, uid, private_gid, allow_file=True):
                raise ComponentError(UNSAFE_GIT_DATA)
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
    check_packed_refs(top, lstat, read)


def check_packed_refs(top, lstat, read):
    path = top + "/" + PACKED_REFS
    try:
        lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise ComponentError(UNSAFE_GIT_DATA) from None
    try:
        data = read(path)
    except OSError:
        raise ComponentError(UNSAFE_GIT_DATA) from None
    if REPLACE_REFS.encode() in data:
        raise ComponentError(UNSAFE_GIT_DATA)


def private_directory(path, uid, lstat):
    """屬於 uid、只有他能存取的目錄，而且每一層上層都安全（不放寬私人群組）。"""
    try:
        info = lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & OTHERS \
        and safe_chain(path, uid, lstat)


def stage_parent(env, uid, lstat):
    """暫存目錄的上層：安全的 XDG_RUNTIME_DIR，否則 root 擁有且有黏著位元的 /tmp。"""
    runtime = env.get("XDG_RUNTIME_DIR", "")
    if runtime.startswith("/") and private_directory(runtime, uid, lstat):
        return runtime
    try:
        info = lstat(TMP)
    except OSError:
        info = None
    if info is not None and stat.S_ISDIR(info.st_mode) and info.st_uid == 0 \
            and info.st_mode & stat.S_ISVTX:
        return TMP
    raise ComponentError(NO_PRIVATE_TEMP)


def check_stage(stage, uid, lstat):
    if not private_directory(str(stage), uid, lstat):
        raise ComponentError(UNSAFE_STAGE)


def check_repository(root, git_base, git, uid, lstat, private_gid, listdir=os.listdir,
                     read=read_bytes):
    """原始碼資料夾、.git、git 目錄與共用 git 目錄（以及上層）只有使用者或 root 能寫，
    git 目錄裡的每一項也一樣。"""
    root = str(root)
    if not safe_chain(root, uid, lstat, private_gid) or \
            not safe_chain(root + "/.git", uid, lstat, private_gid, allow_file=True):
        raise ComponentError(UNSAFE_REPOSITORY)
    git_dir = git(git_base + ["rev-parse", "--absolute-git-dir"]).decode("utf-8", "replace")
    common = git(git_base + ["rev-parse", "--git-common-dir"]).decode("utf-8", "replace")
    common = os.path.normpath(os.path.join(root, common.strip()))
    git_dir = git_dir.strip()
    for path in (git_dir, common):
        if not safe_chain(path, uid, lstat, private_gid):
            raise ComponentError(UNSAFE_REPOSITORY)
    tops = [common] + ([] if git_dir == common or git_dir.startswith(common + "/") else [git_dir])
    for top in tops:
        scan_git_dir(top, uid, private_gid, lstat, listdir, read)


def head_text(action, raw):
    """「git log -1 --format='%h %s'」的輸出 → 顯示用的一行（去掉控制字元並截短）。"""
    text = "".join(character for character in raw.decode("utf-8", "replace").strip()
                   if character.isprintable())
    return HEAD_TEXT[action].format(text[:HEAD_MAX_CHARS])
