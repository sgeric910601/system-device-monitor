"""一鍵更新的協調（只在主執行緒呼叫）：排程、檢查、通知、下載驗證與安裝。

- 開啟程式 15 秒後看一次，之後每 6 小時看一次；只有「自動檢查更新」開著、而且距離上次成功的檢查
  超過 24 小時（或時鐘倒退）才連到 GitHub。上次檢查的時間與通知過的版本存在 XDG 狀態目錄的
  update-state.json。
- 自動檢查只看正式版，同一個版本只通知一次（「有新版本時通知」關閉時不通知）；手動「立即檢查」
  也列出比較新的 Beta、從不通知，但看過的正式版算已通知。自動檢查失敗不顯示錯誤，手動才顯示。
- 網路與驗證都在工作執行緒（services.submit），結果經由 services.on_main 回到主執行緒。
- 安裝只用在 .deb：工作執行緒下載並驗證（update_install.prepare），再以 pkexec 執行 sdm-update
  （逾時 300 秒）；成功時呼叫 hooks.installed（看板重新啟動），失敗時保留新版本並說明原因。
  沒有內建公鑰時一律拒絕安裝。暫存目錄在 pkexec 結束後刪除。
模擬測試與示範模式不建立這個物件（示範模式用 update_demo）。
"""
from dataclasses import dataclass, replace
import json
import logging
import math
import shutil
import time
from typing import Callable, NamedTuple, Optional

from sdm.constants import Update
from sdm.core.jsonfile import load_json, write_json_atomic
from sdm.core.update_logic import ReleaseInfo, choose, releases, should_notify
from sdm.data.github import NETWORK, FetchError
from sdm.services import update_verify
from sdm.services.update_install import NO_KEYS, UNSIGNED, UpdateError

LOG = logging.getLogger(__name__)
KIND_DEB, KIND_SOURCE = "deb", "source"
IDLE, CHECKING, LATEST, AVAILABLE = "idle", "checking", "latest", "available"
DOWNLOADING, INSTALLING, RESTARTING, FAILED = "downloading", "installing", "restarting", "failed"
BUSY = (CHECKING, DOWNLOADING, INSTALLING, RESTARTING)
CHECK_FAILED = "檢查失敗："
INSTALL_FAILED = "安裝失敗："
NOTHING = "GitHub 上找不到可以下載的版本"
TIMEOUT = "逾時：授權或安裝超過 5 分鐘沒有完成；若仍在進行，完成後請重新開啟看板"
UNAUTHORIZED = "未授權：已取消授權或密碼不正確"
WRONG_RESULT = "更新元件回報的版本不符，請重新開啟看板確認目前的版本"
EXIT_TEXT = {2: "更新元件不接受這些參數，請重新安裝 .deb 套件",
             4: "沒有權限：更新必須經由管理員授權"}
REASON_TEXT = {
    "location": "更新元件的位置不受信任，請重新安裝 .deb 套件",
    "input": "下載的暫存檔不安全或不完整",
    "not-deb": "這個安裝不是 .deb 套件",
    "no-keys": NO_KEYS,
    "staging": "無法建立安全的暫存目錄（/var/cache/system-device-monitor）",
    "signature": "簽章驗證失敗",
    "checksum": "下載的 .deb 與簽章過的檢查碼不符",
    "package": "下載的套件與這個程式或版本不符",
    "not-newer": "下載的版本沒有比已安裝的版本新",
    "contents": "套件含有不允許的檔案，已拒絕安裝",
    "busy": "套件管理程式忙碌中（例如正在自動更新），請稍後再試",
    "apt": "apt-get 安裝失敗（詳情見 /var/log/apt/term.log）",
    "apt-timeout": ("apt-get 超過 30 分鐘仍未完成，已要求它停止；套件可能只裝了一半，"
                    "請執行 sudo dpkg --configure -a 後再試"),
    "timeout": "更新元件執行逾時",
}
HELPER_FAILED = "更新元件執行失敗（詳情見系統日誌）"
UNAUTHORIZED_CODES = (126, 127)
LAST_CHECK, LAST_NOTIFIED = "last_check", "last_notified"


@dataclass(frozen=True)
class UpdateState:
    phase: str = IDLE
    release: Optional[ReleaseInfo] = None
    error: str = ""                     # 檢查或安裝失敗的說明
    last_check: Optional[float] = None  # 上次成功檢查的時間（epoch 秒）


class Services(NamedTuple):
    """外部依賴（測試注入）。"""
    fetch: Callable         # fetch() → API 的 JSON（工作執行緒）
    prepare: Callable       # prepare(release) → Prepared（工作執行緒，可能拋出 UpdateError）
    submit: Callable        # submit(function) → Future
    on_main: Callable       # on_main(function, *args)：回到主執行緒
    schedule: Callable      # schedule(秒, callback) → 代號；callback 回傳 True 時重複
    cancel: Callable
    runner: object          # sdm.services.privileged.HelperRunner
    clock: Callable = time.time
    cleanup: Callable = None    # cleanup(暫存目錄)


class Hooks(NamedTuple):
    on_change: Callable     # 狀態改變（設定視窗更新）
    notify: Callable        # notify(release)：自動檢查發現新的正式版
    installed: Callable     # 安裝成功：重新啟動看板
    preferences: Callable   # 目前的 Preferences（auto_check_updates、update_notification）


def remove_directory(path):
    shutil.rmtree(path, ignore_errors=True)


class UpdateMemory:
    """update-state.json：上次成功檢查的時間與通知過的版本；path 為 None 時只在記憶體。"""

    def __init__(self, path):
        self.path = path
        data = load_json(path) if path is not None else {}
        check, notified = data.get(LAST_CHECK), data.get(LAST_NOTIFIED)
        valid = type(check) in (int, float) and math.isfinite(check)
        self.last_check = float(check) if valid else None
        self.last_notified = notified if isinstance(notified, str) else None

    def save(self, **changes):
        for key, value in changes.items():
            setattr(self, key, value)
        if self.path is None:
            return
        try:
            write_json_atomic(self.path, {LAST_CHECK: self.last_check,
                                          LAST_NOTIFIED: self.last_notified})
        except OSError as error:
            LOG.warning("Cannot save the update state: %s", type(error).__name__)


def single_json(text):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    try:
        value = json.loads(lines[0]) if len(lines) == 1 else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def failure_text(status, output):
    """pkexec 或 sdm-update 失敗時給使用者看的說明。"""
    if status is None:
        return TIMEOUT
    if status in UNAUTHORIZED_CODES:
        return UNAUTHORIZED
    if status == 3:
        result = single_json(output) or {}
        return REASON_TEXT.get(result.get("reason"), HELPER_FAILED)
    return EXIT_TEXT.get(status, f"{HELPER_FAILED}（結束碼 {status}）")


class Updater:
    def __init__(self, services, hooks, current, kind, keys=Update.PUBLIC_KEYS, memory=None,
                 root=None):
        """current 為執行中的版本（AppVersion）；kind 為 .deb 或原始碼資料夾；root 為程式所在位置。"""
        self.services, self.hooks, self.current, self.kind = services, hooks, current, kind
        self.keys, self.root = tuple(keys), root
        self.memory = memory or UpdateMemory(None)
        self.state = UpdateState(last_check=self.memory.last_check)
        self.generation, self.stopped, self.timers = 0, False, []

    @property
    def keys_configured(self):
        return bool(update_verify.usable_keys(self.keys))

    @property
    def busy(self):
        return self.state.phase in BUSY

    @property
    def installable(self):
        release = self.state.release
        return self.kind == KIND_DEB and self.keys_configured and self.state.phase == AVAILABLE \
            and release is not None and release.signed

    def update(self, **changes):
        state = replace(self.state, **changes)
        if state != self.state and not self.stopped:
            self.state = state
            self.hooks.on_change()

    # ── 排程 ──

    def start(self):
        self.timers.append(self.services.schedule(Update.FIRST_CHECK_SECONDS, self.first_tick))

    def first_tick(self):
        self.tick()
        if not self.stopped:
            self.timers.append(self.services.schedule(Update.CHECK_INTERVAL_SECONDS, self.tick))
        return False

    def tick(self):
        if self.stopped:
            return False
        if self.hooks.preferences().auto_check_updates and self.due():
            self.check(manual=False)
        return True

    def due(self):
        last, now = self.memory.last_check, self.services.clock()
        return last is None or now < last or now - last > Update.MIN_CHECK_GAP_SECONDS

    def stop(self):
        self.stopped = True
        for timer in self.timers:
            self.services.cancel(timer)
        self.timers = []

    # ── 檢查 ──

    def check_now(self):
        """設定視窗的「立即檢查」：不看 24 小時的閘門，也列出 Beta。"""
        return self.check(manual=True)

    def check(self, manual):
        if self.stopped or self.busy:
            return False
        self.generation += 1
        generation, resting = self.generation, self.state
        self.update(phase=CHECKING, error="")
        future = self.services.submit(self.services.fetch)
        future.add_done_callback(lambda done: self.services.on_main(
            self.checked, generation, manual, resting, done))
        return True

    def checked(self, generation, manual, resting, future):
        if self.stopped or generation != self.generation:
            return False
        try:
            found = releases(future.result())
        except FetchError as error:
            self.check_failed(manual, resting, str(error))
            return False
        except Exception as error:      # 工作執行緒的任何失敗都只算檢查失敗
            LOG.warning("Update check failed: %s", type(error).__name__)
            self.check_failed(manual, resting, NETWORK)
            return False
        now = self.services.clock()
        self.memory.save(last_check=now)
        if not found:
            self.check_failed(manual, resting, NOTHING, last_check=now)
            return False
        self.found(manual, found, now)
        return False

    def check_failed(self, manual, resting, message, **changes):
        if manual:
            self.update(phase=FAILED, release=None, error=CHECK_FAILED + message, **changes)
        else:
            self.update(phase=resting.phase if resting.phase not in BUSY else IDLE,
                        release=resting.release, error=resting.error, **changes)

    def found(self, manual, found, now):
        choice = choose(found, self.current)
        release = choice.beta if manual and choice.beta and not choice.stable else choice.stable
        if release is None:
            self.update(phase=LATEST, release=None, last_check=now)
            return
        self.update(phase=AVAILABLE, release=release, last_check=now)
        if release.is_beta or not should_notify(release.numbers, self.memory.last_notified):
            return
        self.memory.save(last_notified=release.tag)
        if not manual and self.hooks.preferences().update_notification:
            self.hooks.notify(release)

    # ── 安裝 ──

    def install(self):
        """下載、驗證並以 pkexec 安裝目前列出的新版本（只有 .deb）。"""
        release = self.state.release
        if self.stopped or self.kind != KIND_DEB or self.state.phase != AVAILABLE \
                or release is None:
            return False
        if not self.keys_configured or not release.signed:
            self.update(error=INSTALL_FAILED + (UNSIGNED if self.keys_configured else NO_KEYS))
            return False
        self.generation += 1
        generation = self.generation
        self.update(phase=DOWNLOADING, error="")
        future = self.services.submit(lambda: self.services.prepare(release))
        future.add_done_callback(lambda done: self.services.on_main(
            self.prepared, generation, release, done))
        return True

    def prepared(self, generation, release, future):
        if self.stopped or generation != self.generation:
            return False
        try:
            files = future.result()
        except UpdateError as error:
            self.install_failed(release, str(error))
            return False
        except Exception as error:
            LOG.warning("Update download failed: %s", type(error).__name__)
            self.install_failed(release, NETWORK)
            return False
        self.update(phase=INSTALLING)
        argv = [Update.PKEXEC, Update.HELPER, "install", str(files.deb), str(files.sums),
                str(files.signature)]
        self.services.runner.run(argv, Update.PKEXEC_TIMEOUT, lambda status, output:
                                 self.finished(generation, release, files, status, output))
        return False

    def finished(self, generation, release, files, status, output):
        (self.services.cleanup or remove_directory)(files.directory)
        if self.stopped or generation != self.generation:
            return
        result = single_json(output)
        if status == 0 and result is not None and result.get("ok") is True \
                and result.get("version") == release.debian_version:
            self.update(phase=RESTARTING, error="")
            self.hooks.installed()
            return
        self.install_failed(release, WRONG_RESULT if status == 0 else
                            failure_text(status, output))

    def install_failed(self, release, message):
        self.update(phase=AVAILABLE, release=release, error=INSTALL_FAILED + message)
