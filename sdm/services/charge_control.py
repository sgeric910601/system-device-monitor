"""充電上限的使用者端：找出管理員小程式、讀取目前的上限，以 pkexec 寫入並讀回確認。

狀態（ChargeState.status）：
- unsupported：沒有可調整充電上限的系統電池（滑桿不顯示）。
- not-installed：電池支援，但管理員小程式沒有安裝或版本不符（顯示到設定的連結）。
- checking：正在確認小程式的通訊協定；idle：可以調整；applying：等待授權或小程式；
  applied：剛套用；failed：沒有套用（message 寫出原因）。
寫入：
- 使用者拖動 → CommitDebouncer（放開或停 0.6 秒）→ pkexec 小程式 set <電池> <上限>
  --persist on|off（依「開機與喚醒後保留」），逾時 120 秒；成功後再以一般權限讀 sysfs 確認
  （最多 3 次、間隔 0.3 秒）。驅動不接受某個值時改用實際套用的值，並從選項移除被拒絕的值。
- 自動寫入（之後的智慧充電）排在使用者的調整後面：使用者正在拖動、等待送出或正在寫入時
  每 0.1 秒確認一次，最多等 10 秒；不帶 --persist，不改變使用者保留的值與重新套用的單元。
不需要授權就能知道的事（是否支援、目前的上限、是否保留）都以一般權限讀取，
開啟程式時不會要求密碼。「開機與喚醒後保留」要狀態檔屬於這顆電池、而且單元已啟用
（以一般權限執行 systemctl is-enabled）才算開啟；兩者不一致時 persist_mismatch 為 True。
所有方法都在主執行緒呼叫。
"""
from dataclasses import dataclass, replace
import os
from pathlib import Path
import time
from typing import Optional

from gi.repository import GLib

from sdm.constants import ChargeLimit, CpuPower
from sdm.core.charge_limit import (DEFAULT_OPTIONS, CommitDebouncer, nearest, normalized,
                                   refine_options)
from sdm.data.charge_thresholds import find_battery, read_saved, read_thresholds
from sdm.services.privileged import (KIND_DEB, KIND_LOCAL, failure_text, parse_protocol,
                                     parse_result, trusted_path)

UNSUPPORTED, NOT_INSTALLED, CHECKING = "unsupported", "not-installed", "checking"
IDLE, APPLYING, APPLIED, FAILED = "idle", "applying", "applied", "failed"
NOT_APPLIED, PERSIST_FAILED, BUSY = "not-applied", "persist-failed", "busy"
EXIT_FAILED = 3
READ_BACK = "已執行，但讀回的上限是 {}"
REFUSED = "這顆電池不接受 {}%，目前是 {}；可以改選 {}%"
PERSIST_TEXT = "上限已套用，但無法變更「開機與喚醒後保留」"
BUSY_TEXT = "另一個充電上限的調整正在進行，請稍後再試"
UNREADABLE = "讀不到"


@dataclass(frozen=True)
class LimitLayout:
    kind: str                   # deb／local
    helper: str                 # pkexec 執行的小程式
    state: str                  # 保留的狀態檔
    unit: str                   # 開機與喚醒後重新套用的單元


LAYOUTS = (LimitLayout(KIND_DEB, ChargeLimit.DEB_HELPER, ChargeLimit.DEB_STATE,
                       ChargeLimit.DEB_UNIT),
           LimitLayout(KIND_LOCAL, ChargeLimit.LOCAL_HELPER, ChargeLimit.LOCAL_STATE,
                       ChargeLimit.LOCAL_UNIT))


def discover(lstat=os.lstat):
    """第一個受信任的安裝（.deb 優先）；都沒有時為 None。"""
    return next((layout for layout in LAYOUTS if trusted_path(layout.helper, lstat)), None)


def percent_text(value):
    return UNREADABLE if value is None else f"{value}%"


@dataclass(frozen=True)
class ChargeState:
    supply: Optional[str] = None        # 可調整的電池；None 為不支援
    limit: int = ChargeLimit.MAX_LIMIT  # 目前的上限（100＝不限制）
    start: Optional[int] = None
    has_start: bool = False
    options: tuple = DEFAULT_OPTIONS
    draft: Optional[int] = None         # 拖動中、還沒寫入的值
    persist: bool = False               # 「開機與喚醒後保留」：狀態檔與單元都是開啟
    persist_mismatch: bool = False      # 狀態檔與單元不一致（保留設定未生效）
    layout: Optional[LimitLayout] = None
    protocol: Optional[bool] = None     # None：確認中或沒有安裝；False：版本不符
    busy: bool = False
    outcome: str = ""                   # 上一次寫入的結果：applied／failed
    message: str = ""
    conflicts: tuple = ()               # sdm.data.charge_conflicts.Conflict
    demo: bool = False

    @property
    def ready(self):
        if self.supply is None:
            return False
        return self.demo or (self.layout is not None and self.protocol is True)

    @property
    def status(self):
        if self.supply is None:
            return UNSUPPORTED
        if self.busy:
            return APPLYING
        if not self.ready:
            return CHECKING if self.layout is not None and self.protocol is None \
                else NOT_INSTALLED
        return self.outcome or IDLE

    @property
    def shown(self):
        return self.limit if self.draft is None else self.draft


def glib_schedule(seconds, callback):
    return GLib.timeout_add(int(seconds * 1000), callback)


def glib_cancel(source):
    if GLib.MainContext.default().find_source_by_id(source):
        GLib.source_remove(source)


class ChargeControl:
    """on_change() 在狀態改變後呼叫；runner 為 sdm.services.privileged.HelperRunner。"""

    def __init__(self, runner, on_change, root=Path("/"), lstat=os.lstat,
                 schedule=glib_schedule, cancel=glib_cancel, wall=time.time):
        self.runner, self.on_change, self.root, self.lstat = runner, on_change, Path(root), lstat
        self.schedule, self.cancel, self.wall = schedule, cancel, wall
        self.state, self.generation, self.stopped = ChargeState(), 0, False
        self.debouncer = CommitDebouncer(schedule, cancel, self.commit)
        self.queue, self.automatic, self.poll_timer, self.read_timer = [], False, None, None
        self.written_at = None
        self.saved, self.unit_enabled, self.unit_generation = False, None, 0

    def update(self, **changes):
        state = replace(self.state, **changes)
        if state != self.state and not self.stopped:
            self.state = state
            self.on_change()

    # ── 讀取 ───────────────────────────────────────────────

    def refresh(self):
        """重新尋找小程式（確認通訊協定）與電池；開啟程式時呼叫。"""
        layout = discover(self.lstat)
        self.generation += 1
        self.update(layout=layout, protocol=None)
        self.reload()
        if layout is not None:
            generation = self.generation
            self.runner.run([layout.helper, "--protocol"], ChargeLimit.PROTOCOL_TIMEOUT,
                            lambda status, output: self.checked(generation, status, output))

    def checked(self, generation, status, output):
        if generation == self.generation:
            self.update(protocol=status == 0 and parse_protocol(output) == ChargeLimit.PROTOCOL)

    def state_path(self):
        layout = self.state.layout
        return None if layout is None else self.root/layout.state.lstrip("/")

    def reload(self):
        """以一般權限重新讀取門檻與保留狀態；寫入中或拖動中不改變上限。"""
        battery = find_battery(self.root)
        if battery is None:
            self.saved, self.unit_enabled = False, None
            self.update(supply=None, persist=False, persist_mismatch=False)
            return
        changes = dict(supply=battery.supply, has_start=battery.has_start)
        if not self.state.busy and self.debouncer.draft is None:
            changes.update(limit=normalized(battery.end), start=battery.start)
        self.update(**changes)
        self.reload_persist()

    def reload_persist(self):
        """狀態檔是否屬於這顆電池，再以一般權限查詢單元是否啟用（非同步）。"""
        saved = read_saved(self.state_path())
        self.saved = saved is not None and saved.supply == self.state.supply
        self.unit_generation += 1
        layout = self.state.layout
        if layout is None or self.state.supply is None:
            self.unit_enabled = None
            self.apply_persist()
            return
        generation = self.unit_generation
        self.runner.run([ChargeLimit.SYSTEMCTL, "is-enabled", "--quiet", layout.unit],
                        ChargeLimit.UNIT_CHECK_TIMEOUT,
                        lambda status, _output: self.unit_checked(generation, status))

    def unit_checked(self, generation, status):
        if generation != self.unit_generation or self.stopped:
            return
        self.unit_enabled = None if status is None else status == 0
        self.apply_persist()

    def apply_persist(self):
        enabled = self.unit_enabled
        self.update(persist=self.saved and enabled is True,
                    persist_mismatch=enabled is not None and self.saved != enabled)

    def observe(self, power):
        """新的耗電快照：使用者沒有在調整時，跟著系統的上限（例如被其他程式改掉）。"""
        battery = getattr(power, "battery", None)
        if not getattr(battery, "limit_supported", False) or self.state.supply is None:
            return
        if self.state.busy or self.debouncer.draft is not None:
            return
        if self.written_at is not None and power.stamp < self.written_at:
            return
        self.update(limit=normalized(battery.limit))

    def set_conflicts(self, conflicts):
        self.update(conflicts=tuple(conflicts))

    # ── 使用者的調整 ───────────────────────────────────────

    def drag(self, value):
        if self.state.outcome == FAILED:
            self.update(outcome="", message="")
        self.debouncer.drag(value)
        self.update(draft=self.debouncer.draft)

    def set_editing(self, editing):
        self.debouncer.set_editing(editing)
        self.update(draft=self.debouncer.draft)

    def commit(self, value):
        """CommitDebouncer 送出的值：沒有變化或還不能寫入時丟掉草稿。"""
        if not self.state.ready or value == self.state.limit:
            self.debouncer.clear()
            self.update(draft=None)
            return
        self.write(value, self.state.persist, lambda ok: self.user_done(value, ok))

    def user_done(self, value, ok):
        if ok:
            self.debouncer.finished(value)
        else:
            self.debouncer.clear()
        self.update(draft=self.debouncer.draft)

    def set_persist(self, wanted):
        """「開機與喚醒後保留」：以目前的上限重新寫入並開啟或關閉保留。"""
        state = self.state
        if not state.ready or self.user_active() or bool(wanted) == state.persist:
            self.on_change()                # 讓開關回到實際的狀態
            return
        self.debouncer.busy = True
        self.write(state.limit, bool(wanted), lambda ok: self.other_done())

    def other_done(self):
        """不是使用者拖動的寫入結束：寫入期間又拖到別的位置時，送出新的值。"""
        self.debouncer.finished(None)
        self.update(draft=self.debouncer.draft)

    # ── 自動寫入（智慧充電）───────────────────────────────

    def user_active(self):
        return self.state.busy or self.debouncer.editing or self.debouncer.draft is not None

    def apply_automatically(self, target, done):
        """done(成功, 讀回的上限)；排在使用者的調整與其他自動寫入後面。"""
        if not self.state.ready or target not in self.state.options:
            done(False, None)
            return
        polls = round(ChargeLimit.AUTOMATIC_WAIT / ChargeLimit.AUTOMATIC_POLL)
        self.queue.append([target, done, polls])
        self.next_automatic()

    def next_automatic(self):
        if self.stopped or self.automatic or not self.queue:
            return
        if self.user_active():
            if self.poll_timer is None:
                self.poll_timer = self.schedule(ChargeLimit.AUTOMATIC_POLL, self.poll_automatic)
            return
        target, done, _ = self.queue.pop(0)
        if target == self.state.limit:
            done(True, target)
            self.next_automatic()
            return
        self.automatic, self.debouncer.busy = True, True
        self.write(target, None, lambda ok: self.automatic_done(done, ok))

    def poll_automatic(self):
        """每 0.1 秒：使用者還在調整就把等待次數減一，用完就放棄這一筆。"""
        if self.stopped or not self.queue:
            self.poll_timer = None
            return False
        if self.user_active():
            self.queue[0][2] -= 1
            if self.queue[0][2] > 0:
                return True
            _, done, _ = self.queue.pop(0)
            done(False, self.state.limit)
            if self.queue:
                return True
        self.poll_timer = None
        self.next_automatic()
        return False

    def automatic_done(self, done, ok):
        self.automatic = False
        done(ok, self.state.limit)
        self.other_done()
        self.next_automatic()

    # ── 寫入與確認 ─────────────────────────────────────────

    def write(self, target, persist, finished):
        """pkexec 小程式 set；persist 為 None 時不帶 --persist（保留狀態不變）。
        finished(是否成功) 只呼叫一次。"""
        state = self.state
        argv = [CpuPower.PKEXEC, state.layout.helper, "set", state.supply, str(target)]
        if persist is not None:
            argv += ["--persist", "on" if persist else "off"]
        self.update(busy=True, outcome="", message="")
        self.runner.run(argv, ChargeLimit.PKEXEC_TIMEOUT,
                        lambda status, output: self.helper_done(target, status, output,
                                                                finished))

    def helper_done(self, target, status, output, finished):
        if self.stopped:
            return
        result = parse_result(output)
        if status == 0 and result is not None and result["ok"]:
            self.confirm(target, ChargeLimit.READBACK_ATTEMPTS, finished)
        else:
            self.failed(target, status, result, finished)

    def confirm(self, target, attempts, finished):
        """以一般權限讀回 sysfs：相符就完成，否則 0.3 秒後再讀，最多 3 次。"""
        self.read_timer = None
        if self.stopped:
            return False
        reading = read_thresholds(self.root, self.state.supply)
        end = None if reading is None else reading.end
        if end == target or (attempts <= 1):
            ok = end == target
            self.finish(ok, normalized(end), "" if ok else READ_BACK.format(percent_text(end)),
                        finished)
            return False
        self.read_timer = self.schedule(ChargeLimit.READBACK_INTERVAL,
                                        lambda: self.confirm(target, attempts - 1, finished))
        return False

    def failed(self, target, status, result, finished):
        error = result.get("error") if result else None
        applied = result.get("applied") if result else None
        if status == EXIT_FAILED and error == NOT_APPLIED:
            options = refine_options(self.state.options, target, applied)
            self.update(options=options)
            message = REFUSED.format(target, percent_text(applied), nearest(target, options))
            self.finish(False, normalized(applied), message, finished)
        elif status == EXIT_FAILED and error == PERSIST_FAILED:
            self.finish(False, normalized(applied), PERSIST_TEXT, finished)
        elif status == EXIT_FAILED and error == BUSY:
            self.finish(False, self.state.limit, BUSY_TEXT, finished)
        else:
            reading = read_thresholds(self.root, self.state.supply)
            limit = self.state.limit if reading is None else normalized(reading.end)
            self.finish(False, limit, failure_text(status, result), finished)

    def finish(self, ok, limit, message, finished):
        self.written_at = self.wall()
        self.update(busy=False, outcome=APPLIED if ok else FAILED, message=message, limit=limit)
        self.reload_persist()
        finished(ok)

    def stop(self):
        self.stopped = True
        self.debouncer.stop()
        for source in (self.poll_timer, self.read_timer):
            if source is not None:
                self.cancel(source)


class DemoChargeControl:
    """示範模式：不尋找元件、不執行任何程式，所有調整只改記憶體中的值。"""

    def __init__(self, on_change, schedule, cancel, limit=80, start=75):
        self.on_change = on_change
        self.state = ChargeState(supply="BAT0", limit=limit, start=start, has_start=True,
                                 demo=True)
        self.debouncer = CommitDebouncer(schedule, cancel, self.commit)

    def update(self, **changes):
        self.state = replace(self.state, **changes)
        self.on_change()

    def commit(self, value):
        self.debouncer.finished(value)
        self.update(limit=value, draft=None, outcome=APPLIED)

    def drag(self, value):
        self.debouncer.drag(value)
        self.update(draft=self.debouncer.draft, outcome="")

    def set_editing(self, editing):
        self.debouncer.set_editing(editing)
        self.update(draft=self.debouncer.draft)

    def set_persist(self, wanted):
        self.update(persist=bool(wanted))

    def apply_automatically(self, target, done):
        self.update(limit=target)
        done(True, target)

    def set_conflicts(self, conflicts):
        self.update(conflicts=tuple(conflicts))

    def refresh(self):
        self.on_change()

    def reload(self):
        return None

    def observe(self, _power):
        return None

    def stop(self):
        self.debouncer.stop()
