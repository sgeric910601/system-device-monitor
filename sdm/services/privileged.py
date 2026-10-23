"""管理員元件：找出已安裝的 CPU 功耗取樣元件、確認通訊協定，並以 pkexec 非同步開關。

- 尋找順序：.deb 的 /usr/libexec，其次原始碼安裝的 /usr/local/libexec。小程式必須是一般檔案
  （不是符號連結），它與每一層上層目錄都屬於 root 且群組與其他人不可寫，否則視為沒有安裝。
- 通訊協定：以一般權限執行「小程式 --protocol」（逾時 5 秒）；版本不符時提示重新安裝，
  不提供開關。
- 開關：pkexec 小程式 enable｜disable（Gio.Subprocess，非同步，逾時 120 秒）；
  逾時就不再等待並嘗試結束 pkexec。結束碼 126／127 是未授權，2／3／4 是小程式回報的錯誤。
- 是否已開啟：socket 存在而且屬於自己（drop-in 的 SocketUser）；不需要授權即可判斷，
  開啟程式時不會跳出密碼視窗。
"""
from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Optional

from gi.repository import Gio, GLib

from sdm.constants import CpuPower

LOG = logging.getLogger(__name__)
KIND_DEB, KIND_LOCAL = "deb", "local"
SOCKET_MINE, SOCKET_OTHER, SOCKET_ABSENT = "mine", "other", "absent"
UNSAFE_BITS = stat.S_IWGRP | stat.S_IWOTH
REINSTALL = "需要重新安裝管理員元件（版本不符）"
TIMEOUT = "逾時：授權或管理員元件超過 2 分鐘沒有完成"
UNAUTHORIZED = "未授權：已取消授權或密碼不正確"
NOT_APPLIED = "已執行，但 CPU 取樣服務沒有依設定開啟或關閉"
HELPER_FAILURES = {
    2: "管理員元件不接受這個參數，可能需要重新安裝管理員元件",
    3: "管理員元件執行失敗（詳情見系統日誌）",
    4: "沒有權限：管理員元件必須經由管理員授權執行",
}


@dataclass(frozen=True)
class Layout:
    kind: str                   # deb／local
    helper: str                 # pkexec 執行的小程式
    socket: str                 # 取樣服務的 socket


LAYOUTS = (Layout(KIND_DEB, CpuPower.DEB_HELPER, CpuPower.DEB_SOCKET),
           Layout(KIND_LOCAL, CpuPower.LOCAL_HELPER, CpuPower.LOCAL_SOCKET))
DEMO_ROOT = Path("/opt/system-device-monitor")     # 示範模式顯示的原始碼位置（不是這台電腦的）


def root_owned(info):
    return info.st_uid == 0 and not info.st_mode & UNSAFE_BITS


def trusted_path(path, lstat=os.lstat):
    """一般檔案，它與每一層上層目錄都屬於 root 且群組與其他人不可寫。"""
    pure = PurePosixPath(path)
    if not pure.is_absolute():
        return False
    try:
        info = lstat(str(pure))
        if not stat.S_ISREG(info.st_mode) or not root_owned(info):
            return False
        for parent in pure.parents:
            info = lstat(str(parent))
            if not stat.S_ISDIR(info.st_mode) or not root_owned(info):
                return False
    except OSError:
        return False
    return True


def discover(lstat=os.lstat):
    """第一個受信任的安裝；都沒有時為 None。"""
    return next((layout for layout in LAYOUTS if trusted_path(layout.helper, lstat)), None)


def socket_state(path, lstat=os.lstat, uid=None):
    try:
        info = lstat(path)
    except OSError:
        return SOCKET_ABSENT
    if not stat.S_ISSOCK(info.st_mode):
        return SOCKET_ABSENT
    return SOCKET_MINE if info.st_uid == (os.getuid() if uid is None else uid) else SOCKET_OTHER


def single_json(text):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    try:
        value = json.loads(lines[0])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def parse_protocol(text):
    value = single_json(text)
    protocol = value.get("protocol") if value else None
    return protocol if type(protocol) is int else None


def parse_result(text):
    value = single_json(text)
    return value if value is not None and type(value.get("ok")) is bool else None


def failure_text(status, _result):
    """pkexec 或小程式失敗時給使用者看的說明；status 為 None 代表逾時或無法執行。"""
    if status is None:
        return TIMEOUT
    if status in (126, 127):
        return UNAUTHORIZED
    return HELPER_FAILURES.get(status, f"管理員元件執行失敗（結束碼 {status}）")


class HelperRunner:
    """以 Gio.Subprocess 非同步執行；done(結束碼或 None, 標準輸出) 只會呼叫一次。

    逾時後立即回報 None 並嘗試結束程序；之後才結束的結果一律丟棄
    （pkexec 已切換成 root 的小程式無法由一般使用者結束，但介面不再等待）。
    """

    def __init__(self, timeout_add=GLib.timeout_add_seconds, source_remove=GLib.source_remove):
        self.timeout_add, self.source_remove = timeout_add, source_remove

    def run(self, argv, timeout_s, done):
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        try:
            process = Gio.Subprocess.new(argv, flags)
        except GLib.Error as error:
            LOG.warning("Cannot start an admin helper: %s", error.domain)
            done(None, "")
            return
        call = {"done": done, "timer": 0}
        call["timer"] = self.timeout_add(timeout_s, self.expired, process, call)
        process.communicate_utf8_async(None, None, self.finished, call)

    def expired(self, process, call):
        call["timer"] = 0
        process.force_exit()
        self.report(call, None, "")
        return False

    def finished(self, process, result, call):
        if call["timer"]:
            self.source_remove(call["timer"])
            call["timer"] = 0
        try:
            _, output, _ = process.communicate_utf8_finish(result)
        except GLib.Error:
            self.report(call, None, "")
            return
        status = process.get_exit_status() if process.get_if_exited() else None
        self.report(call, status, output or "")

    @staticmethod
    def report(call, status, output):
        done, call["done"] = call["done"], None
        if done is not None:
            done(status, output)


@dataclass(frozen=True)
class AdminState:
    layout: Optional[Layout] = None
    protocol: Optional[bool] = None     # None：確認中或沒有安裝；False：版本不符
    socket: str = SOCKET_ABSENT
    busy: bool = False                  # 正在等待授權或小程式
    error: str = ""                     # 最後一次的錯誤說明

    @property
    def kind(self):
        return self.layout.kind if self.layout else None

    @property
    def ready(self):
        return self.layout is not None and self.protocol is True

    @property
    def enabled(self):
        return self.socket == SOCKET_MINE


class AdminComponents:
    """管理員元件的狀態與開關；on_change() 在狀態改變後呼叫（都在主執行緒）。"""

    def __init__(self, runner, on_change, lstat=os.lstat, uid=os.getuid, root=None):
        """root 為程式所在位置（設定視窗顯示原始碼資料夾的安裝指令用）。"""
        self.runner, self.on_change, self.lstat, self.uid = runner, on_change, lstat, uid
        self.root = root
        self.state, self.generation, self.stopped = AdminState(), 0, False

    def update(self, **changes):
        state = replace(self.state, **changes)
        if state != self.state and not self.stopped:
            self.state = state
            self.on_change()

    def socket_of(self, layout):
        return socket_state(layout.socket, self.lstat, self.uid()) if layout else SOCKET_ABSENT

    def refresh(self):
        """重新尋找元件並確認通訊協定（非同步）。"""
        layout = discover(self.lstat)
        self.generation += 1
        self.update(layout=layout, protocol=None, socket=self.socket_of(layout),
                    error="" if self.state.protocol is not False else self.state.error)
        if layout is not None:
            generation = self.generation
            self.runner.run([layout.helper, "--protocol"], CpuPower.PROTOCOL_TIMEOUT,
                            lambda status, output: self.checked(generation, status, output))

    def checked(self, generation, status, output):
        if generation != self.generation:
            return
        ok = status == 0 and parse_protocol(output) == CpuPower.PROTOCOL
        self.update(protocol=ok, error="" if ok else REINSTALL)

    def update_socket(self):
        self.update(socket=self.socket_of(self.state.layout))

    def set_enabled(self, wanted, done):
        """以 pkexec 開啟或關閉；done(是否成功)。狀態已經相符時不要求授權。"""
        if self.stopped or self.state.busy or not self.state.ready:
            done(False)
            return
        self.update_socket()
        if self.state.enabled == wanted:
            done(True)
            return
        self.update(busy=True, error="")
        argv = [CpuPower.PKEXEC, self.state.layout.helper, "enable" if wanted else "disable"]
        self.runner.run(argv, CpuPower.PKEXEC_TIMEOUT,
                        lambda status, output: self.switched(wanted, done, status, output))

    def switched(self, wanted, done, status, output):
        if self.stopped:
            return
        result = parse_result(output)
        socket = self.socket_of(self.state.layout)
        applied = (socket == SOCKET_MINE) == wanted
        ok = status == 0 and result is not None and result["ok"] and applied
        error = "" if ok else (NOT_APPLIED if status == 0 else failure_text(status, result))
        self.update(busy=False, socket=socket, error=error)
        done(ok)

    def report(self, text):
        """由看板補充的說明（例如開啟程式時關閉了選項），顯示在「上次的錯誤」。"""
        self.update(error=text)

    def stop(self):
        self.stopped = True


class DemoAdmin:
    """示範模式：不尋找元件、不執行任何程式，只在記憶體中切換。"""

    def __init__(self, on_change, state):
        self.on_change, self.state, self.root = on_change, state, DEMO_ROOT

    def update(self, **changes):
        self.state = replace(self.state, **changes)
        self.on_change()

    def refresh(self):
        self.on_change()

    def update_socket(self):
        return None

    def set_enabled(self, wanted, done):
        self.update(socket=SOCKET_MINE if wanted else SOCKET_ABSENT, error="")
        done(True)

    def report(self, text):
        self.update(error=text)

    def stop(self):
        return None


def demo_admin(scenario, on_change):
    """桌機示範：原始碼安裝且已開啟（與示範的 CPU 項目一致）；筆電示範：尚未安裝。"""
    if scenario == "desktop":
        layout = Layout(KIND_LOCAL, CpuPower.LOCAL_HELPER, CpuPower.LOCAL_SOCKET)
        return DemoAdmin(on_change, AdminState(layout, True, SOCKET_MINE))
    return DemoAdmin(on_change, AdminState())
