"""原始碼資料夾的管理員元件（CPU 功耗取樣、充電上限、定時喚醒與無人授權開關）：
install.py --system-components install｜remove 的使用者端。

以一般使用者執行（不需要、也不接受 sudo）：
1. 原始碼資料夾與 git 目錄（含上層）只能由使用者或 root 寫入（見 install_guard）；
   本地的 git 設定不得含 include、includeIf、filter 或 core.attributesFile。
2. 以 git ls-tree 找出 HEAD 中這些檔案的 blob（只接受 100644／100755 的一般檔案），
   以 git cat-file blob 讀出原始內容並以物件 ID 驗證：不套用任何 filter、export-subst、ident
   或換行轉換，也不使用 git archive 或 git status。
3. 工作樹必須與 HEAD 相同：自己計算工作樹檔案的 blob 物件 ID 比對（不經過 git，不會觸發
   clean filter）；安裝的一定是 HEAD 的內容。內容寫到新的私人暫存目錄（XDG_RUNTIME_DIR 底下，
   目錄 0700、檔案 0600、O_EXCL、O_NOFOLLOW）。
4. 每個檔案的 SHA-256 寫成 manifest.json；確認暫存目錄與上層都安全、顯示 HEAD 的提交後，執行
   pkexec /usr/bin/python3 -I -B -c BOOTSTRAP <暫存>/scripts/install_system.py <它的 SHA-256>
   install｜remove --stage <暫存> --manifest-sha256 <manifest 的 SHA-256>。
   啟動碼只執行雜湊相符的那份位元組；root 端再驗證 manifest 與每個檔案的雜湊。
5. 結束後刪除暫存目錄。已安裝 .deb 套件時不安裝也不移除（兩者擇一；原始碼版的移除會撤銷
   兩者共用的無人授權規則並取消喚醒鬧鐘，root 端也再確認一次）。
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile

from sdm.services.install_guard import (BOOTSTRAP, ComponentError, GroupPolicy,  # noqa: F401
                                        MountTable, check_repository, check_stage, head_text,
                                        read_bytes, stage_parent, system_listxattr,
                                        user_private_gid)

SOURCES = (
    "helper/sdm-cpu-power",
    "helper/sdm-cpu-power-service",
    "helper/policy/tw.local.system-device-monitor.local.policy",
    "helper/systemd/system-device-monitor-local-cpu-power.socket",
    "helper/systemd/system-device-monitor-local-cpu-power.service",
    "helper/sdm-charge-limit",
    "helper/policy/tw.local.system-device-monitor.unattended.local.charge-limit.policy",
    "helper/systemd/system-device-monitor-local-charge-limit.service",
    "helper/sdm-wake",
    "helper/policy/tw.local.system-device-monitor.unattended.local.wake.policy",
    "helper/sdm-unattended",
    "helper/policy/tw.local.system-device-monitor.local.rules.policy",
)
INSTALLER = "scripts/install_system.py"
STAGED = SOURCES + (INSTALLER,)
BLOB_MODES = ("100644", "100755")
OBJECT_FORMATS = {"sha1": 40, "sha256": 64}
FORBIDDEN_CONFIG = ("include.", "includeif.", "filter.")
FORBIDDEN_CONFIG_KEYS = ("core.attributesfile",)
TRUE_VALUES = ("true", "yes", "on", "1", "")    # git 的布林真值（只寫鍵名也算真）
HEX = re.compile(r"[0-9a-f]+")
ACTIONS = ("install", "remove")
GIT = "/usr/bin/git"
# 使用者端 git 指令的固定選項與環境：不執行 hooks、不用 fsmonitor、不連任何遠端，
# 不讀系統與使用者的設定檔、不套用 replace 物件與系統的屬性檔，也不繼承任何 GIT_* 變數。
SAFE_GIT_OPTIONS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                    "-c", "protocol.allow=never", "-c", "core.attributesFile=/dev/null")
SAFE_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ATTR_NOSYSTEM": "1"}
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
DONE = {"install": ("已安裝管理員元件。要開啟 CPU 功耗取樣：設定 → 管理員功能；"
                    "筆電的充電上限與智慧充電在「電源」頁調整。"),
        "remove": "已移除管理員元件。"}
FAILURES = {
    2: "安裝程式的參數錯誤，沒有做任何變更。",
    3: "管理員元件安裝程式回報失敗（錯誤代號在上一行），沒有完成變更。",
    4: "沒有權限：管理員元件必須由 pkexec 以管理員身分安裝。",
}
UNAUTHORIZED_TEXT = "未授權：管理員授權被取消或失敗，沒有做任何變更。"
DIRTY = "管理員元件的檔案有未提交的修改；請先提交或還原，安裝的一定是已提交的版本。"
UNTRACKED = "找不到已提交的管理員元件檔案；需要以 git 取得的原始碼資料夾。"
BAD_TREE = "HEAD 中的管理員元件不是一般檔案，或內容與物件 ID 不符，已停止。"
UNSAFE_CONFIG = ("原始碼資料夾的 git 設定含 include、includeIf、filter 或 core.attributesFile，"
                 "讀取時可能執行指令或改變內容；為了安全不安裝。")
DEB_INSTALLED = "已安裝 .deb 套件：管理員元件已包含在套件中，不需要再從原始碼安裝。"
DEB_INSTALLED_REMOVE = ("已安裝 .deb 套件，沒有移除任何東西：原始碼版的移除會撤銷套件也在用的"
                        "「允許無人時自動調整」規則並取消喚醒鬧鐘。要改用套件時，請先執行"
                        " sudo apt remove system-device-monitor，再執行"
                        " install.py --system-components remove，之後重新安裝套件。")
NO_PKEXEC = "無法執行 pkexec（需要 policykit-1／polkitd）。"
STAGE_FAILED = "無法建立暫存目錄，沒有做任何變更。"


def command_for(root, action):
    script = shlex.quote(str(Path(root)/"install.py"))
    return f"{PYTHON} -B {script} --system-components {action}"


def install_command(root):
    """設定視窗顯示、可以直接貼到終端機的安裝指令。"""
    return command_for(root, "install")


def remove_command(root):
    return command_for(root, "remove")


def git_command(root, *args):
    return [GIT, *SAFE_GIT_OPTIONS, "-C", str(root), *args]


def git_environment(environ):
    """去掉所有繼承的 GIT_* 變數（含 GIT_DIR、GIT_WORK_TREE），再加上固定的安全設定。"""
    env = {key: value for key, value in environ.items() if not key.startswith("GIT_")}
    env.update(SAFE_GIT_ENV)
    return env


def git_output(argv, environ=None):
    try:
        return subprocess.run(argv, capture_output=True, check=True,
                              env=git_environment(os.environ if environ is None else environ)).stdout
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


def config_entries(root, git, scope):
    """（小寫的鍵、值）；指定檔案範圍時 git 不展開 include，也不含 -c 傳入的設定。"""
    entries = []
    for item in git(git_command(root, "config", scope, "--list", "-z")).split(b"\0"):
        key, _, value = item.decode("utf-8", "replace").partition("\n")
        if key.strip():
            entries.append((key.strip().lower(), value.strip().lower()))
    return entries


def check_local_config(root, git):
    """本地的 git 設定（啟用 extensions.worktreeConfig 時再加上 config.worktree）不得含會執行
    指令或改變內容的鍵。沒有啟用時 git 不讀 config.worktree，--worktree 在多個工作樹時也會失敗。"""
    entries = config_entries(root, git, "--local")
    switch = [value for key, value in entries if key == "extensions.worktreeconfig"]
    if switch and switch[-1] in TRUE_VALUES:
        entries += config_entries(root, git, "--worktree")
    for key, _ in entries:
        if key.startswith(FORBIDDEN_CONFIG) or key in FORBIDDEN_CONFIG_KEYS:
            raise ComponentError(UNSAFE_CONFIG)


def object_format(root, git):
    name = git(git_command(root, "rev-parse", "--show-object-format")).decode("ascii",
                                                                             "replace").strip()
    if name not in OBJECT_FORMATS:
        raise ComponentError(BAD_TREE)
    return name


def object_id(data, algorithm):
    """git 的 blob 物件 ID（「blob 長度\\0」＋內容的雜湊）。"""
    return hashlib.new(algorithm, b"blob %d\0" % len(data) + data).hexdigest()


def head_blobs(root, git, algorithm):
    """HEAD 中每個要安裝的檔案 → 物件 ID；必須剛好一次、而且是一般檔案的 blob。"""
    listing = git(git_command(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *STAGED))
    blobs = {}
    for item in listing.split(b"\0"):
        if not item:
            continue
        meta, _, raw_name = item.partition(b"\t")
        fields = meta.decode("ascii", "replace").split(" ")
        name = raw_name.decode("utf-8", "replace")
        if len(fields) != 3 or fields[0] not in BLOB_MODES or fields[1] != "blob" \
                or len(fields[2]) != OBJECT_FORMATS[algorithm] or not HEX.fullmatch(fields[2]) \
                or name not in STAGED or name in blobs:
            raise ComponentError(BAD_TREE)
        blobs[name] = fields[2]
    if set(blobs) != set(STAGED):
        raise ComponentError(UNTRACKED)
    return blobs


def check_clean(root, blobs, algorithm, lstat, read):
    """工作樹的檔案必須是一般檔案，而且內容與 HEAD 的 blob 相同。"""
    for name, oid in blobs.items():
        path = str(Path(root)/name)
        try:
            if not stat.S_ISREG(lstat(path).st_mode) or object_id(read(path), algorithm) != oid:
                raise ComponentError(DIRTY)
        except OSError:
            raise ComponentError(DIRTY) from None


def read_blobs(root, git, blobs, algorithm):
    """以 cat-file blob 讀出原始內容，並確認內容符合物件 ID。"""
    contents = {}
    for name, oid in blobs.items():
        data = git(git_command(root, "cat-file", "blob", oid))
        if object_id(data, algorithm) != oid:
            raise ComponentError(BAD_TREE)
        contents[name] = data
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


def pkexec_argv(stage, action, digest, script_digest):
    """root 以啟動碼驗證並執行暫存的 install_system.py；參數清單，從不經過 shell。"""
    stage = str(stage)
    return [PKEXEC, PYTHON, "-I", "-B", "-c", BOOTSTRAP, str(Path(stage)/INSTALLER), script_digest,
            action, "--stage", stage, "--manifest-sha256", digest]


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


PRIVATE_GROUP = object()        # run() 的預設：查詢使用者私人群組


def gather(action, root, git, uid, policy, files):
    """檢查原始碼資料夾、取出 HEAD 的檔案與要顯示的提交；不安全時拋出 ComponentError。
    files 為（lstat、listdir、read）；policy 為私人群組的放寬（None：不放寬）。"""
    check_repository(root, git_command(root), git, uid, files[0], policy, *files[1:])
    check_local_config(root, git)
    algorithm = object_format(root, git)
    blobs = head_blobs(root, git, algorithm)
    check_clean(root, blobs, algorithm, files[0], files[2])
    contents = read_blobs(root, git, blobs, algorithm)
    return contents, head_text(action, git(git_command(root, "log", "-1", "--format=%h %s",
                                                       "HEAD")))


def run(action, root, say, git=git_output, call=subprocess.call, make_stage=None,
        deb_installed=package_installed, lstat=os.lstat, uid=None, private_gid=PRIVATE_GROUP,
        listdir=os.listdir, read=read_bytes, listxattr=system_listxattr, fstype=None):
    """安裝或移除；回傳結束碼（pkexec 的結束碼，或使用者端拒絕時的 1）。"""
    uid = os.getuid() if uid is None else uid
    if private_gid is PRIVATE_GROUP:
        private_gid = user_private_gid(uid)
    policy = None if private_gid is None else GroupPolicy(
        private_gid, listxattr, fstype or MountTable.load().fstype)
    if make_stage is None:
        make_stage = lambda: tempfile.mkdtemp(prefix=STAGE_PREFIX,  # noqa: E731
                                              dir=stage_parent(os.environ, uid, lstat))
    if deb_installed():
        say(DEB_INSTALLED if action == "install" else DEB_INSTALLED_REMOVE)
        return REFUSED
    try:
        contents, head = gather(action, root, git, uid, policy, (lstat, listdir, read))
        stage = make_stage()
    except ComponentError as error:
        say(str(error))
        return REFUSED
    try:
        status = stage_and_call(stage, contents, action, call, say, head, uid, lstat)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if status is None:
        return REFUSED
    say(result_text(action, status))
    return status


def stage_and_call(stage, contents, action, call, say, head, uid, lstat):
    """寫出暫存檔、確認暫存目錄安全並顯示提交，再執行 pkexec；回傳它的結束碼，
    在本機就失敗時說明原因並回傳 None。"""
    try:
        digest = prepare(stage, contents)
        check_stage(stage, uid, lstat)
    except OSError:
        say(STAGE_FAILED)
        return None
    except ComponentError as error:
        say(str(error))
        return None
    say(head)
    script_digest = hashlib.sha256(contents[INSTALLER]).hexdigest()
    try:
        return call(pkexec_argv(stage, action, digest, script_digest))
    except OSError:
        say(NO_PKEXEC)
        return None
