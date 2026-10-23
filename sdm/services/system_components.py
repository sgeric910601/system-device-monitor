"""原始碼資料夾的管理員元件：install.py --system-components install｜remove 的使用者端。

以一般使用者執行（不需要、也不接受 sudo）：
1. 要安裝的檔案必須已提交到 git 且沒有修改，否則拒絕；安裝的一定是 HEAD 的內容。
2. 以 git archive HEAD 取出這些檔案；tar 成員只能是預期的一般檔案與它們的上層目錄，
   內容讀進記憶體後自己寫到新的私人暫存目錄（目錄 0700、檔案 0600、O_EXCL、O_NOFOLLOW），
   從不使用 tarfile 的解壓縮功能。
3. 每個檔案的 SHA-256 寫成 manifest.json，再把 manifest 本身的 SHA-256 當參數執行
   pkexec /usr/bin/python3 -I -B <暫存>/scripts/install_system.py；root 端重新驗證所有雜湊。
4. 結束後刪除暫存目錄。已安裝 .deb 套件時不安裝（兩者擇一），但可以移除。
"""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import tarfile
import tempfile

SOURCES = (
    "helper/sdm-cpu-power",
    "helper/sdm-cpu-power-service",
    "helper/policy/tw.local.system-device-monitor.local.policy",
    "helper/systemd/system-device-monitor-local-cpu-power.socket",
    "helper/systemd/system-device-monitor-local-cpu-power.service",
)
INSTALLER = "scripts/install_system.py"
STAGED = SOURCES + (INSTALLER,)
DIRECTORIES = frozenset(str(parent) for name in STAGED
                        for parent in PurePosixPath(name).parents if str(parent) != ".")
ACTIONS = ("install", "remove")
GIT = "/usr/bin/git"
PKEXEC = "/usr/bin/pkexec"
PYTHON = "/usr/bin/python3"
DPKG_QUERY = "/usr/bin/dpkg-query"
PACKAGE = "system-device-monitor"
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
STAGE_PREFIX = "sdm-system-"
PRIVATE_DIRECTORY = 0o700
PRIVATE_FILE = 0o600
REFUSED = 1                     # 使用者端拒絕或無法執行時的結束碼
UNAUTHORIZED = (126, 127)       # pkexec：取消授權、未授權或驗證失敗
DONE = {"install": "已安裝管理員元件。要開啟 CPU 功耗取樣：設定 → 管理員功能。",
        "remove": "已移除管理員元件。"}
FAILURES = {
    2: "安裝程式的參數錯誤，沒有做任何變更。",
    3: "管理員元件安裝程式回報失敗（錯誤代號在上一行），沒有完成變更。",
    4: "沒有權限：管理員元件必須由 pkexec 以管理員身分安裝。",
}
UNAUTHORIZED_TEXT = "未授權：管理員授權被取消或失敗，沒有做任何變更。"
DIRTY = "管理員元件的檔案有未提交的修改；請先提交或還原，安裝的一定是已提交的版本。"
UNTRACKED = "找不到已提交的管理員元件檔案；需要以 git 取得的原始碼資料夾。"
BAD_ARCHIVE = "git archive 的內容不符合預期，已停止。"
DEB_INSTALLED = "已安裝 .deb 套件：管理員元件已包含在套件中，不需要再從原始碼安裝。"
NO_PKEXEC = "無法執行 pkexec（需要 policykit-1／polkitd）。"
STAGE_FAILED = "無法建立暫存目錄，沒有做任何變更。"


class ComponentError(Exception):
    """無法安全地準備安裝；訊息是給使用者看的說明。"""


def command_for(root, action):
    script = shlex.quote(str(Path(root)/"install.py"))
    return f"{PYTHON} -B {script} --system-components {action}"


def install_command(root):
    """設定視窗顯示、可以直接貼到終端機的安裝指令。"""
    return command_for(root, "install")


def remove_command(root):
    return command_for(root, "remove")


def git_output(argv):
    try:
        return subprocess.run(argv, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise ComponentError(UNTRACKED) from None


def package_installed():
    """以一般使用者查詢 dpkg：套件仍有檔案（不是未安裝或只剩設定檔）時為 True。"""
    try:
        result = subprocess.run([DPKG_QUERY, "-W", "-f=${Status}", PACKAGE],
                                capture_output=True, text=True, check=False)
    except OSError:
        return False
    words = result.stdout.split()
    return result.returncode == 0 and len(words) == 3 and \
        words[2] not in ("not-installed", "config-files")


def check_clean(root, git):
    base = [GIT, "-C", str(root)]
    git(base + ["ls-files", "--error-unmatch", "--", *STAGED])
    if git(base + ["status", "--porcelain", "--untracked-files=no", "--", *STAGED]).strip():
        raise ComponentError(DIRTY)


def export(root, git):
    """HEAD 中這些檔案的內容；tar 裡有任何預期以外的成員就拒絕。"""
    data = git([GIT, "-C", str(root), "archive", "--format=tar", "HEAD", "--", *STAGED])
    contents = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive.getmembers():
                name = member.name.rstrip("/") if member.isdir() else member.name
                if member.isdir() and name in DIRECTORIES:
                    continue
                if not member.isreg() or name not in STAGED or name in contents:
                    raise ComponentError(BAD_ARCHIVE)
                contents[name] = archive.extractfile(member).read()
    except tarfile.TarError:
        raise ComponentError(BAD_ARCHIVE) from None
    if set(contents) != set(STAGED):
        raise ComponentError(BAD_ARCHIVE)
    return contents


def write_private(stage, name, data):
    path = Path(stage)/name
    path.parent.mkdir(mode=PRIVATE_DIRECTORY, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 PRIVATE_FILE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def manifest_bytes(contents):
    files = {name: hashlib.sha256(contents[name]).hexdigest() for name in SOURCES}
    return json.dumps({"format": MANIFEST_FORMAT, "files": files}, indent=2,
                      sort_keys=True).encode("utf-8") + b"\n"


def pkexec_argv(stage, action, digest):
    stage = str(stage)
    return [PKEXEC, PYTHON, "-I", "-B", str(Path(stage)/INSTALLER), action, "--stage", stage,
            "--manifest-sha256", digest]


def prepare(stage, contents):
    """寫出暫存檔與 manifest；回傳 manifest 的 SHA-256。"""
    os.chmod(stage, PRIVATE_DIRECTORY)
    for name, data in contents.items():
        write_private(stage, name, data)
    manifest = manifest_bytes(contents)
    write_private(stage, MANIFEST_NAME, manifest)
    return hashlib.sha256(manifest).hexdigest()


def result_text(action, status):
    if status == 0:
        return DONE[action]
    if status in UNAUTHORIZED:
        return UNAUTHORIZED_TEXT
    return FAILURES.get(status, f"管理員元件安裝程式失敗（結束碼 {status}）。")


def run(action, root, say, git=git_output, call=subprocess.call,
        make_stage=lambda: tempfile.mkdtemp(prefix=STAGE_PREFIX),
        deb_installed=package_installed):
    """安裝或移除；回傳結束碼（pkexec 的結束碼，或使用者端拒絕時的 1）。"""
    if action == "install" and deb_installed():
        say(DEB_INSTALLED)
        return REFUSED
    try:
        check_clean(root, git)
        contents = export(root, git)
    except ComponentError as error:
        say(str(error))
        return REFUSED
    stage = make_stage()
    try:
        status = stage_and_call(stage, contents, action, call, say)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if status is None:
        return REFUSED
    say(result_text(action, status))
    return status


def stage_and_call(stage, contents, action, call, say):
    """寫出暫存檔並執行 pkexec；回傳它的結束碼，在本機就失敗時說明原因並回傳 None。"""
    try:
        digest = prepare(stage, contents)
    except OSError:
        say(STAGE_FAILED)
        return None
    try:
        return call(pkexec_argv(stage, action, digest))
    except OSError:
        say(NO_PKEXEC)
        return None
