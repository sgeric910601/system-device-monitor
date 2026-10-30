"""智慧充電「精準喚醒」的看板端（移植參考專案 Data/ChargingWakeScheduler.swift 的排定規則）。

參考專案以管理員服務呼叫 IOPMSchedulePowerEvent（macOS 才有，也會被暗喚醒提早叫醒）；Linux 改用
sdm-wake 小程式寫 RTC 喚醒鬧鐘（/sys/class/rtc/rtc0/wakealarm），經由 pkexec 執行。
- 只在排程選了精準喚醒、小程式可用（通訊協定相符），而且已允許無人時自動調整時才排定；
  否則不執行任何程式，排程在睡眠前改用準備優先（看板顯示原因）。
- 喚醒時間只能在 60 秒後到 7 天內；與已排定的時間相差不到 1 秒視為同一個，相差 5 分鐘以上
  才重排；不需要時取消。失敗後 1 分鐘內不再試。
- 小程式要求兩次 set 至少相隔 10 秒：距離上次 set 不到 10 秒時，先等滿再執行最新的要求；
  取消（clear）不受限，立即執行。同一時間只執行一個。關機或撤銷授權前（clear_now）不等失敗後的
  1 分鐘；正在設定鬧鐘時排在它後面，設定完再取消（不會留下鬧鐘）。
- 已排定的鬧鐘以一般權限讀 wakealarm 確認還在；響過或被別人換掉就不算排好。
所有方法都在主執行緒呼叫。
"""
from dataclasses import dataclass, replace
import os
import time
from typing import Optional

from gi.repository import GLib

from sdm.constants import CpuPower, SmartCharging, Wake
from sdm.core.charging_plan import wake_allowed, wake_should_replace
from sdm.data.sources import read
from sdm.services.privileged import (KIND_DEB, KIND_LOCAL, parse_protocol, parse_result,
                                     trusted_path)

OFF, MISSING, NOT_GRANTED, IDLE = "off", "missing", "not-granted", "idle"
WAITING, SCHEDULED, FAILED = "waiting", "scheduled", "failed"
UNAUTHORIZED, TIMEOUT, BROKEN = "unauthorized", "timeout", "failed"
RETRY_ERRORS = ("busy", "rate-limited")     # 稍等就會好：不必等 1 分鐘


@dataclass(frozen=True)
class WakeLayout:
    kind: str
    helper: str


LAYOUTS = (WakeLayout(KIND_DEB, Wake.DEB_HELPER), WakeLayout(KIND_LOCAL, Wake.LOCAL_HELPER))


def discover(lstat=os.lstat):
    return next((layout for layout in LAYOUTS if trusted_path(layout.helper, lstat)), None)


def read_rtc_alarm(path=Wake.RTC_ALARM):
    """目前的 RTC 喚醒時間（所有人可讀）；沒有鬧鐘或讀不到時為 None。"""
    text = read(path)
    return int(text) if text.isascii() and text.isdigit() else None


def error_code(status, result):
    if status is None:
        return TIMEOUT
    if status in (126, 127):
        return UNAUTHORIZED
    error = result.get("error") if result else None
    return error if isinstance(error, str) and error else BROKEN


def glib_schedule(seconds, callback):
    return GLib.timeout_add(int(seconds * 1000), callback)


def glib_cancel(source):
    if GLib.MainContext.default().find_source_by_id(source):
        GLib.source_remove(source)


@dataclass(frozen=True)
class WakeState:
    layout: Optional[WakeLayout] = None
    protocol: Optional[bool] = None
    scheduled: Optional[int] = None     # 這個看板排定的喚醒時間
    busy: bool = False
    note: str = OFF
    error: str = ""                     # 上次失敗的代號（小程式的 error、unauthorized 等）
    demo: bool = False

    @property
    def component(self):
        """小程式已安裝而且通訊協定相符。"""
        return self.demo or (self.layout is not None and self.protocol is True)


class WakeControl:
    def __init__(self, runner, on_change, lstat=os.lstat, schedule=glib_schedule,
                 cancel=glib_cancel, clock=time.monotonic, wall=time.time,
                 read_alarm=read_rtc_alarm):
        self.runner, self.on_change, self.lstat = runner, on_change, lstat
        self.schedule, self.cancel, self.clock, self.wall = schedule, cancel, clock, wall
        self.read_alarm = read_alarm
        self.state, self.generation, self.stopped = WakeState(), 0, False
        self.target = (None, False, False)  # 最新的（喚醒時間、精準喚醒、已授權）
        self.last_set = self.retry_at = self.timer = None
        self.waiting = []                   # 正在執行時收到的 clear_now，結束後再取消

    def update(self, **changes):
        state = replace(self.state, **changes)
        if state != self.state and not self.stopped:
            self.state = state
            self.on_change()

    def refresh(self):
        layout = discover(self.lstat)
        self.generation += 1
        self.update(layout=layout, protocol=None)
        if layout is not None:
            generation = self.generation
            self.runner.run([layout.helper, "--protocol"], Wake.PROTOCOL_TIMEOUT,
                            lambda status, output: self.checked(generation, status, output))

    def checked(self, generation, status, output):
        if generation == self.generation:
            self.update(protocol=status == 0 and parse_protocol(output) == Wake.PROTOCOL)

    def ready(self):
        """已排定、還在 30 秒以後，而且 RTC 上的鬧鐘就是它：睡眠前不必先提高上限。"""
        scheduled = self.state.scheduled
        return scheduled is not None \
            and scheduled - self.wall() > SmartCharging.WAKE_READY_SECONDS \
            and self.read_alarm() == scheduled

    # ── 依排程同步 ─────────────────────────────────────────

    def sync(self, wake_at, enabled, granted):
        """每次判斷排程後呼叫：依選項與排程決定排定、改排或取消喚醒。"""
        self.target = (wake_at, bool(enabled), bool(granted))
        if self.stopped or self.state.busy or self.timer is not None:
            return
        if self.state.scheduled is not None and self.read_alarm() != self.state.scheduled:
            self.update(scheduled=None)             # 已經響過或被別人換掉
        if not enabled:
            self.cancel_or(OFF, granted)
        elif not self.state.component:
            self.update(note=MISSING)
        elif not granted:
            self.update(note=NOT_GRANTED)
        else:
            self.follow(wake_at)

    def follow(self, wake_at):
        now, existing = self.wall(), self.state.scheduled
        if wake_at is not None and wake_at > now and existing is not None \
                and abs(existing - wake_at) < SmartCharging.WAKE_KEEP_SECONDS:
            return
        if wake_at is None or not wake_allowed(wake_at, now):
            self.cancel_or(IDLE, True)
            return
        if wake_should_replace(existing, wake_at):
            self.run(["set", str(int(wake_at))])

    def cancel_or(self, note, granted):
        """有排定的鬧鐘就取消（需要授權，否則會跳出密碼視窗，所以沒有授權時不動）。"""
        if self.state.scheduled is not None and granted and self.state.component:
            self.run(["clear"])
        else:
            self.update(note=note)

    # ── 執行小程式 ─────────────────────────────────────────

    def run(self, arguments, done=None, urgent=False):
        if self.retry_at is not None and self.clock() < self.retry_at and not urgent:
            return
        wait = self.gap_left() if arguments[0] == "set" else 0
        if wait > 0:
            self.timer = self.schedule(wait, self.gap_passed)
            return
        self.update(busy=True, note=WAITING)
        argv = [CpuPower.PKEXEC, self.state.layout.helper, *arguments]
        self.runner.run(argv, Wake.PKEXEC_TIMEOUT,
                        lambda status, output: self.finished(arguments, status, output, done))

    def gap_left(self):
        """距離上次 set 還要等的秒數（小程式只限制兩次 set 的間隔）。"""
        return 0 if self.last_set is None else self.last_set + Wake.MIN_WRITE_GAP - self.clock()

    def gap_passed(self):
        self.timer = None
        self.sync(*self.target)
        return False

    def finished(self, arguments, status, output, done):
        if self.stopped:
            return
        if arguments[0] == "set":
            self.last_set = self.clock()
        result = parse_result(output)
        if status == 0 and result is not None and result["ok"]:
            setting = arguments[0] == "set"
            self.retry_at = None
            self.update(busy=False, scheduled=int(arguments[1]) if setting else None,
                        note=SCHEDULED if setting else IDLE, error="")
        else:
            code = error_code(status, result)
            if code not in RETRY_ERRORS:
                self.retry_at = self.clock() + SmartCharging.RETRY_SECONDS
            scheduled = self.state.scheduled if arguments[0] == "clear" else None
            self.update(busy=False, scheduled=scheduled, note=FAILED, error=code)
        if done is not None:
            done()
        if self.waiting:
            waiting, self.waiting = self.waiting, []
            self.clear_now(lambda: [callback() for callback in waiting])
        elif done is None:
            self.sync(*self.target)

    def clear_now(self, done):
        """關機或撤銷授權前：有排定的鬧鐘就立即取消（不等失敗後的 1 分鐘）；正在執行時排在它後面。
        done() 在結束後呼叫。"""
        if self.state.busy:
            self.waiting.append(done)
            return
        if self.state.scheduled is None or not self.state.component:
            done()
            return
        self.run(["clear"], done, urgent=True)

    def stop(self):
        self.stopped = True
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None


class DemoWake:
    """示範模式：不執行任何程式，只記下時間。"""

    def __init__(self, on_change, clock=time.time):
        self.on_change, self.clock = on_change, clock
        self.state = WakeState(protocol=True, demo=True)

    def refresh(self):
        self.on_change()

    def sync(self, wake_at, enabled, granted):
        allowed = enabled and granted and wake_at is not None \
            and wake_allowed(wake_at, self.clock())
        state = replace(self.state, scheduled=int(wake_at) if allowed else None,
                        note=SCHEDULED if allowed else (IDLE if enabled else OFF))
        if state != self.state:
            self.state = state
            self.on_change()

    def ready(self):
        scheduled = self.state.scheduled
        return scheduled is not None and scheduled - self.clock() > SmartCharging.WAKE_READY_SECONDS

    def clear_now(self, done):
        done()

    def stop(self):
        return None
