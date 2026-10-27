#!/usr/bin/python3
"""產生發布簽章用的 Ed25519 金鑰（只在維護者自己的電腦上執行一次）。

用法：/usr/bin/python3 -B scripts/release_keys.py generate --out-dir <專案與任何 git 工作樹以外的資料夾>

- 拒絕專案資料夾裡面、以及任何 git 工作樹（上層有 .git）裡的位置，避免私鑰被提交或推送。
- 資料夾不存在時以 0700 建立；已存在時必須是自己的、不是符號連結、而且已經是 0700
  （不改變既有資料夾的權限）。私鑰以 /usr/bin/openssl genpkey -algorithm ED25519 產生，
  以 O_EXCL 寫成 0600 的 PEM，絕不覆寫既有的金鑰。
- 印出 base64 的原始公鑰（貼到 sdm/constants.py 的 Update.PUBLIC_KEYS 與 helper/sdm-update 的
  PUBLIC_KEYS），並提醒備份。
"""
import argparse
import base64
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdm.services.update_verify import raw_public_key  # noqa: E402

OPENSSL = "/usr/bin/openssl"
KEY_NAME = "sdm-release-ed25519.pem"
PUBLIC_NAME = "sdm-release-ed25519.pub"
PRIVATE_DIRECTORY = 0o700
PRIVATE_FILE = 0o600
PUBLIC_FILE = 0o644
PEM_HEADER = b"-----BEGIN " + b"PRIVATE KEY-----"
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
BACKUP = ("這把金鑰是本專案專用的：不要拿來簽其他產品，也絕不沿用 macOS 版（Sparkle）的金鑰。\n"
          "請把整個資料夾備份到離線的位置（例如加密的隨身碟），並記住備份在哪裡。\n"
          "遺失私鑰就無法再發布可以一鍵更新的版本，只能換新金鑰、讓使用者手動安裝一次；\n"
          "私鑰外洩時要立刻換新金鑰並從公鑰陣列移除舊的。私鑰絕不要放進 Git、雲端同步資料夾或專案資料夾。")


class Refused(Exception):
    """不安全的位置或檔案；訊息給維護者看。"""


def git_work_tree(path):
    """path 或它的任何一層上層有 .git（目錄或檔案）時回傳那一層，否則為 None。"""
    for candidate in (path, *path.parents):
        if os.path.lexists(candidate/".git"):
            return candidate
    return None


def check_location(out_dir, root=ROOT):
    """不在專案資料夾裡，也不在任何 git 工作樹裡。"""
    target = Path(os.path.abspath(out_dir))
    resolved = target.resolve()
    project = Path(root).resolve()
    for candidate in (target, resolved):
        if candidate == project or project in candidate.parents:
            raise Refused("不能放在專案資料夾裡：" + str(target))
        if git_work_tree(candidate) is not None:
            raise Refused("不能放在 git 工作樹裡：" + str(git_work_tree(candidate)))


def prepare_directory(out_dir):
    """建立 0700 的資料夾；已存在時必須已經是自己的 0700 資料夾。"""
    try:
        info = os.lstat(out_dir)
    except FileNotFoundError:
        out_dir.mkdir(mode=PRIVATE_DIRECTORY, parents=True)
        os.chmod(out_dir, PRIVATE_DIRECTORY)
        return
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Refused("輸出位置不是自己的資料夾（也不能是符號連結）：" + str(out_dir))
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise Refused("既有的資料夾權限太寬（需要 0700），請改用新的資料夾：" + str(out_dir))


def openssl(argv):
    result = subprocess.run([OPENSSL, *argv], stdin=subprocess.DEVNULL, capture_output=True,
                            check=False)
    if result.returncode != 0:
        raise Refused("openssl 執行失敗：" + argv[0])
    return result.stdout


def write_new(path, data, mode):
    fd = os.open(path, CREATE, mode)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def generate(out_dir, root=ROOT):
    """產生金鑰並回傳 base64 的原始公鑰。"""
    out_dir = Path(out_dir)
    check_location(out_dir, root)
    if os.path.islink(out_dir):
        raise Refused("輸出位置不能是符號連結：" + str(out_dir))
    prepare_directory(out_dir)
    key = out_dir/KEY_NAME
    if os.path.lexists(key) or os.path.lexists(out_dir/PUBLIC_NAME):
        raise Refused("資料夾裡已經有發布金鑰，絕不覆寫：" + str(key))
    pem = openssl(["genpkey", "-algorithm", "ED25519"])
    if not pem.startswith(PEM_HEADER):
        raise Refused("openssl 產生的私鑰格式不符")
    write_new(key, pem, PRIVATE_FILE)
    raw = raw_public_key(openssl(["pkey", "-in", str(key), "-pubout", "-outform", "DER"]))
    if raw is None:
        raise Refused("無法取得 Ed25519 公鑰")
    public = base64.b64encode(raw).decode("ascii")
    write_new(out_dir/PUBLIC_NAME, (public + "\n").encode("ascii"), PUBLIC_FILE)
    return public


def main(argv=None):
    parser = argparse.ArgumentParser(description="產生發布簽章用的 Ed25519 金鑰")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("generate", help="產生新的金鑰")
    command.add_argument("--out-dir", type=Path, required=True,
                         help="專案與任何 git 工作樹以外的資料夾")
    args = parser.parse_args(argv)
    try:
        public = generate(args.out_dir.expanduser())
    except (Refused, OSError) as error:
        raise SystemExit(f"沒有產生金鑰：{error}") from None
    print(f"已產生發布簽章私鑰：{args.out_dir.expanduser()/KEY_NAME}（權限 0600）")
    print("公鑰（貼到 sdm/constants.py 的 Update.PUBLIC_KEYS 與 helper/sdm-update 的 PUBLIC_KEYS）：")
    print(public)
    print(BACKUP)


if __name__ == "__main__":
    main()
