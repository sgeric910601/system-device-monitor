"""「允許無人時自動調整」的看板端：找出 sdm-unattended、確認通訊協定、查詢規則是否生效，
並以 pkexec 開關（每次都要管理員密碼）。

- 是否已授權不讀規則檔（/etc/polkit-1/rules.d 只有 root 與 polkitd 能讀）：以一般權限執行
  pkcheck 查詢沒有任何作用的 unattended.status 動作（預設一律拒絕，規則對這個帳號、本機作用中
  的工作階段生效時才允許）。指定自己的程序時附上啟動時間（pid,start-time,uid），避免 pid 被重複使用。
- 開關：pkexec sdm-unattended grant｜revoke（Gio.Subprocess，非同步，逾時 120 秒），之後再查一次。
- 開啟程式時只尋找與查詢，絕不要求授權。所有方法都在主執行緒呼叫。
"""
from dataclasses import dataclass, replace
import os
from typing import Optional

from sdm.constants import CpuPower, Unattended
from sdm.data.sources import read
from sdm.services.privileged import (KIND_DEB, KIND_LOCAL, failure_text, parse_protocol,
                                     parse_result, trusted_path)

GRANTED = 0                     # pkcheck：已授權；其他結束碼都不算
START_TIME_FIELD = 19           # /proc/<pid>/stat 中「)」之後的第 20 欄（starttime）
ERRORS = {
    "unsupported-user": "你的帳號名稱不是一般的格式（小寫英數、底線或減號），無法建立規則",
    "no-rules-directory": "找不到 /etc/polkit-1/rules.d，這個系統的 polkit 不支援規則檔",
    "unexpected-file": "規則檔的位置已經有別的東西，沒有變更",
    "busy": "另一個授權變更正在進行，請稍後再試",
}


@dataclass(frozen=True)
class RuleLayout:
    kind: str
    helper: str                 # pkexec 執行的小程式
    check: str                  # pkcheck 查詢的動作


LAYOUTS = (RuleLayout(KIND_DEB, Unattended.DEB_HELPER, Unattended.CHECK_DEB),
           RuleLayout(KIND_LOCAL, Unattended.LOCAL_HELPER, Unattended.CHECK_LOCAL))


def discover(lstat=os.lstat):
    return next((layout for layout in LAYOUTS if trusted_path(layout.helper, lstat)), None)


def process_spec(pid, uid, read_text=read):
    """pkcheck --process 的「pid,啟動時間,uid」（程序名稱可能含空白與括號，從最後一個「)」切）。"""
    text = read_text(f"/proc/{pid}/stat") or ""
    fields = text.rsplit(")", 1)[-1].split()
    start = fields[START_TIME_FIELD] if len(fields) > START_TIME_FIELD else ""
    return f"{pid},{start},{uid}" if start.isdigit() else str(pid)


def error_text(status, result):
    error = result.get("error") if result else None
    if status == 3 and error in ERRORS:
        return ERRORS[error]
    return failure_text(status, result)


@dataclass(frozen=True)
class UnattendedState:
    layout: Optional[RuleLayout] = None
    protocol: Optional[bool] = None     # None：確認中或沒有安裝；False：版本不符
    granted: Optional[bool] = None      # None：還不知道
    busy: bool = False
    error: str = ""
    demo: bool = False

    @property
    def ready(self):
        return self.demo or (self.layout is not None and self.protocol is True)


class UnattendedControl:
    """on_change() 在狀態改變後呼叫；runner 為 sdm.services.privileged.HelperRunner。"""

    def __init__(self, runner, on_change, lstat=os.lstat, pid=os.getpid, uid=os.getuid,
                 read=read):
        self.runner, self.on_change, self.lstat = runner, on_change, lstat
        self.pid, self.uid, self.read = pid, uid, read
        self.state, self.generation, self.stopped = UnattendedState(), 0, False

    def update(self, **changes):
        state = replace(self.state, **changes)
        if state != self.state and not self.stopped:
            self.state = state
            self.on_change()

    def refresh(self):
        layout = discover(self.lstat)
        self.generation += 1
        self.update(layout=layout, protocol=None, granted=None if layout else False)
        if layout is not None:
            generation = self.generation
            self.runner.run([layout.helper, "--protocol"], Unattended.PROTOCOL_TIMEOUT,
                            lambda status, output: self.checked(generation, status, output))

    def checked(self, generation, status, output):
        if generation != self.generation:
            return
        ok = status == 0 and parse_protocol(output) == Unattended.PROTOCOL
        self.update(protocol=ok, granted=None if ok else False)
        if ok:
            self.check()

    def check(self, done=None):
        """以 pkcheck 查詢規則是否對這個程序生效；done() 在查完後呼叫。"""
        if not self.state.ready:
            if done is not None:
                done()
            return
        generation = self.generation
        argv = [Unattended.PKCHECK, "--action-id", self.state.layout.check, "--process",
                process_spec(self.pid(), self.uid(), self.read)]
        self.runner.run(argv, Unattended.CHECK_TIMEOUT,
                        lambda status, _output: self.answered(generation, status, done))

    def answered(self, generation, status, done):
        if generation == self.generation:
            self.update(granted=status == GRANTED)
        if done is not None:
            done()

    def set_granted(self, wanted, done):
        """以 pkexec 開啟或關閉；done(是否成功)。狀態已經相符時不要求授權。"""
        state = self.state
        if self.stopped or state.busy or not state.ready:
            done(False)
            return
        if state.granted == bool(wanted):
            done(True)
            return
        self.update(busy=True, error="")
        argv = [CpuPower.PKEXEC, state.layout.helper, "grant" if wanted else "revoke"]
        self.runner.run(argv, Unattended.PKEXEC_TIMEOUT,
                        lambda status, output: self.switched(status, output, done))

    def switched(self, status, output, done):
        if self.stopped:
            return
        result = parse_result(output)
        ok = status == 0 and result is not None and result["ok"]
        self.update(error="" if ok else error_text(status, result))
        self.check(lambda: self.finish(ok, done))

    def finish(self, ok, done):
        self.update(busy=False)
        done(ok)

    def stop(self):
        self.stopped = True


class DemoUnattended:
    """示範模式：不尋找元件、不執行任何程式，只在記憶體中切換。"""

    def __init__(self, on_change, granted=True):
        self.on_change = on_change
        self.state = UnattendedState(protocol=True, granted=granted, demo=True)

    def refresh(self):
        self.on_change()

    def check(self, done=None):
        if done is not None:
            done()

    def set_granted(self, wanted, done):
        self.state = replace(self.state, granted=bool(wanted))
        self.on_change()
        done(True)

    def stop(self):
        return None
