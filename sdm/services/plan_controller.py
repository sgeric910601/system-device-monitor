"""智慧充電的狀態機（移植參考專案 Services/ChargingPlanController.swift，不載入 GTK）。

決定現在要把充電上限設成多少、何時還原、何時通知；實際寫入由呼叫的地方做
（ChargeControl.apply_automatically，從不帶 --persist），寫完以 write_finished 回報，
成功是指讀回的上限等於要求的值。同一時間只有一個寫入；失敗後 60 秒內不重試。
- 使用者自己調整上限（拖動滑桿，或被其他程式改成別的值）時以使用者為準：結束排程、不還原。
  例外：剛從睡眠喚醒（did_wake）時，「開機與喚醒後保留」的服務會把上限改回使用者保留的值；
  排程進行中以排程為準，把那個值當成新的基準並重新寫入排程的上限。服務比喚醒後的判斷晚跑完時，
  喚醒後 30 秒內第一次的改變也這樣處理；之後的改變才是使用者。
- 失敗後的重試與剛提高後的寬限以牆上時鐘計時（寬限要跨重開程式），時鐘往回調時不會被拉長：
  剩下的等待比 1 分鐘還久就視為已過，經過的時間是負的就不再寬限。
- 一次性排程：完成時間過後 2 小時，或已達目標並拔掉電源時，還原原本的上限；還原寫入失敗時
  保留 restoring 並在 1 分鐘後重試，成功才結束。
- 每週排程：完成或過期就換到下一次；還沒到的完成時間也重新算一次，相差 60 秒以上就改
  （日光節約時間、換時區）。
- 提高上限後 2 分鐘內還沒開始充電仍是「準備充電」，不算系統暫停。
- 「已準備好」與「可能趕不上」每一輪（完成時間）只通知一次，重開程式也不重複；
  這一輪沒有提高過上限（建立時就已經達到目標）不通知「已準備好」。
驅動只接受部分上限（PlanInput.options，例如只接受 60、80、100 的機型）時，目標的上限向上取、
保養上限向下取，提高與否、寬限時間與通知都用取過的值判斷；選項改變（寫入被拒絕後縮減）或新排程
的值要取過時寫進紀錄。還原原本的上限不取（接受 50–100 的任何值）。
"""
from dataclasses import replace
from datetime import datetime
import secrets

from sdm.constants import SmartCharging as Limits
from sdm.core import charging_plan as engine
from sdm.core.charging_plan import (CHARGING, LATE, MINUTES_PER_HOUR, READINESS_FIRST, READY,
                                    STARTING, SYSTEM_PAUSED, WAITING, make_plan)

REFRESH, WILL_SLEEP, DID_WAKE = "refresh", "will_sleep", "did_wake"
NOTE_READY, NOTE_LATE = "ready", "late"
EXPIRY_SECONDS = Limits.EXPIRY_SECONDS
RETRY_SECONDS = Limits.RETRY_SECONDS
START_GRACE_SECONDS = Limits.START_GRACE_SECONDS
ID_BYTES = 16
STARTED_PHASES = (STARTING, CHARGING, SYSTEM_PAUSED, LATE)
WRITE_ERROR = "系統沒有套用智慧充電需要的充電上限，1 分鐘後重試"
STRATEGY_TITLES = {READINESS_FIRST: "準備優先", engine.PRECISE_WAKE: "精準喚醒"}
PHASE_TITLES = {engine.UNSUPPORTED: "不支援", engine.UNPLUGGED: "等待電源", WAITING: "等待開始",
                STARTING: "準備充電", CHARGING: "充電中", SYSTEM_PAUSED: "系統暫停",
                READY: "已準備好", LATE: "可能趕不上"}
WEEKDAY_NAMES = "一二三四五六日"


def new_id():
    return secrets.token_hex(ID_BYTES)


class PlanController:
    """只在主執行緒使用；store 為 PlanStore，log(文字) 寫入紀錄檔。"""

    def __init__(self, store, tz=None, log=lambda message: None, make_id=new_id, load=True):
        self.store, self.tz, self.log, self.make_id = store, tz, log, make_id
        self.plan = store.load() if load else None
        self.decision = None
        self.write_error = None
        self.pending = None             # 正在寫入的上限
        self.pending_raise = False      # 這次寫入是把上限提高到目標
        self.retry_at = None            # 寫入失敗後，這個時間之前不再重試
        self.options = None             # 上次判斷時驅動接受的上限（None：還沒判斷過這個排程）
        self.resume_until = None        # 喚醒後這個時間之前第一次的上限改變是保留的服務

    def load(self):
        """讀取存起來的排程（看板只在筆電啟動智慧充電時才讀，桌機不碰排程檔）。"""
        self.plan = self.store.load()

    @property
    def active(self):
        return self.plan is not None

    def moment(self, stamp):
        return datetime.fromtimestamp(stamp, self.tz).strftime("%m/%d %H:%M")

    def describe(self, plan):
        schedule = plan.schedule
        if schedule.weekly:
            hours, minutes = divmod(schedule.minute, MINUTES_PER_HOUR)
            days = "、".join(WEEKDAY_NAMES[day - 1] for day in schedule.days())
            kind = f"每週{days} {hours:02d}:{minutes:02d}"
        else:
            kind = "一次"
        return (f"{kind}，{self.moment(plan.deadline)} 前充到 {plan.target}%，"
                f"保養上限 {plan.hold}%，睡眠策略 {STRATEGY_TITLES[plan.strategy]}")

    # ── 建立、取消、策略 ───────────────────────────────────

    def start(self, schedule, target, hold, current_limit, strategy, now):
        """建立（或取代）排程；取代時沿用第一次記下的原本上限。完成時間已過時回傳 False。"""
        deadline = schedule.next_deadline(now, self.tz)
        if deadline is None:
            return False
        restore = self.plan.restore if self.plan is not None else current_limit
        value = make_plan(schedule, deadline, target, hold, restore, strategy, now,
                          self.make_id())
        self.reset(value)
        self.options = None
        self.log(f"建立排程：{self.describe(value)}，原本上限 {value.restore}%")
        return True

    def cancel(self, data):
        """使用者取消：還原原本的上限；回傳要寫入的值（None：不用寫）。"""
        if self.plan is None:
            return None
        self.log("使用者取消排程")
        return self.begin_restore(self.plan, data)

    def set_strategy(self, strategy):
        if self.plan is None or self.plan.strategy == strategy:
            return
        self.save(replace(self.plan, strategy=strategy))
        self.log(f"睡眠策略改為 {STRATEGY_TITLES[strategy]}")

    def user_changed_limit(self, value):
        """使用者在排程進行中自己調整了充電上限：以使用者為準，結束排程、不還原。"""
        if self.plan is not None:
            self.clear(f"使用者把充電上限改成 {value}%，結束排程（不還原）")

    # ── 判斷 ───────────────────────────────────────────────

    def evaluate(self, data, event=REFRESH, wake_ready=False):
        """依目前狀態判斷；回傳需要寫入的充電上限（None：不用寫）。"""
        if self.plan is None:
            self.decision = None
            return None
        if not data.limit_supported:
            self.decision = engine.evaluate(self.plan, data)
            return None
        self.note_options(self.plan, data.options)
        if event == DID_WAKE:     # 睡前的失敗不必再等 1 分鐘；喚醒後的第一次改變可能是保留的服務
            self.resume_until = data.now + Limits.WAKE_RESTORE_SECONDS
            self.retry_at = None
        if self.plan.restoring:
            return self.restore(self.plan, data)
        if self.overridden(self.plan, data, event):
            return None
        if not self.plan.schedule.weekly:
            reason = self.once_finished(self.plan, data)
            if reason:
                self.log(reason)
                return self.begin_restore(self.plan, data)
        else:
            self.roll_weekly(self.plan, data)
        result = self.observe(self.plan, data, event)
        desired = result.desired_limit
        # 睡著就沒辦法準時提高上限：除非已經排好喚醒，否則睡眠前直接提高到目標，在睡眠中充
        if event == WILL_SLEEP and result.phase == WAITING \
                and (self.plan.strategy == READINESS_FIRST or not wake_ready):
            desired = engine.target_limit(self.plan, data.options)
            self.log(f"即將睡眠，先把上限提高到 {desired}%")
        return None if desired is None else self.request(desired, data)

    def note_options(self, plan, options):
        """驅動接受的上限改變，或新排程的值要取過時，記下實際使用的上限。"""
        if options == self.options:
            return
        changed, self.options = self.options is not None, options
        top, hold = engine.target_limit(plan, options), engine.hold_value(plan, options)
        if changed or top != plan.target or hold != plan.hold:
            accepted = engine.options_text(options) if options else "不限制"
            self.log(f"驅動接受的上限：{accepted}；目標的上限實際 {top}%、保養上限實際 {hold}%")

    def overridden(self, plan, data, event):
        """上限不是排程最後寫入的值：使用者改過（結束排程），或剛喚醒（以排程為準）。"""
        applied = plan.applied
        if applied is None or self.pending is not None or data.current_limit == applied:
            return False
        until, self.resume_until = self.resume_until, None     # 只有第一次改變算保留的服務
        if until is not None and 0 <= until - data.now <= Limits.WAKE_RESTORE_SECONDS:
            self.log(f"喚醒後上限是 {data.current_limit}%（排程設的是 {applied}%），"
                     "排程進行中，改回排程的上限")
            self.save(replace(plan, applied=data.current_limit))
            return False
        self.clear(f"充電上限被改成 {data.current_limit}%（排程設的是 {applied}%），"
                   "結束排程（不還原）")
        return True

    @staticmethod
    def once_finished(plan, data):
        if data.now > plan.deadline + EXPIRY_SECONDS:
            return "完成時間已過 2 小時，結束排程"
        if not data.connected and (plan.completed is not None or data.percent >= plan.target):
            return "已達目標並拔掉電源，結束排程"
        return ""

    def roll_weekly(self, plan, data):
        """完成或過期就換到下一次；還沒到的完成時間也重新對一次（換時區、日光節約時間）。"""
        upcoming = engine.rolled_deadline(plan, data.now, data.percent, self.tz)
        if upcoming is not None:
            self.save(replace(plan, deadline=upcoming, completed=None))
            self.log(f"每週排程換到下一次：{self.moment(upcoming)}")

    def observe(self, plan, data, event):
        result = engine.evaluate(plan, data)
        # 剛提高上限、還沒開始充：仍是「準備充電」，不是系統暫停（時鐘往回調時不再寬限）
        if result.phase == SYSTEM_PAUSED and plan.raised_at is not None \
                and 0 <= data.now - plan.raised_at < START_GRACE_SECONDS:
            result = replace(result, phase=STARTING)
        if self.decision is None or result.phase != self.decision.phase:
            self.log(f"狀態：{PHASE_TITLES[result.phase]}（電量 {data.percent}%，"
                     f"上限 {data.current_limit}%，{'接著電源' if data.connected else '沒接電源'}，"
                     f"需要約 {result.required_minutes} 分鐘，預計 {self.moment(result.start_at)} "
                     f"開始，事件 {event}）")
        changes = {}
        if result.phase == READY and plan.completed is None:
            changes["completed"] = data.now
        if result.phase in STARTED_PHASES and plan.started_for != plan.deadline:
            changes["started_for"] = plan.deadline
        if changes:
            self.save(replace(plan, **changes))
        self.decision = result
        return result

    # ── 寫入 ───────────────────────────────────────────────

    def request(self, value, data):
        """要求寫入 value（判斷時已經取成驅動接受的值；還原時是原本的上限）。"""
        if value == data.current_limit or self.pending is not None:
            return None
        if self.retry_at is not None and 0 < self.retry_at - data.now <= RETRY_SECONDS:
            return None
        plan = self.plan
        self.pending = value
        self.pending_raise = not plan.restoring \
            and value == engine.target_limit(plan, data.options) \
            and value > engine.hold_value(plan, data.options)
        self.log(f"要求把上限從 {data.current_limit}% 改成 {value}%")
        return value

    def write_finished(self, target, success, read_back, now):
        """寫入結束；success 是讀回的上限等於 target。"""
        if self.pending != target:
            return
        raised, self.pending, self.pending_raise = self.pending_raise, None, False
        shown = "無" if read_back is None else f"{read_back}%"
        self.log(f"寫入上限 {target}%：{'成功' if success else '失敗'}，讀回 {shown}")
        if not success:
            self.retry_at = now + RETRY_SECONDS
            self.write_error = WRITE_ERROR
            return
        self.retry_at = self.write_error = None
        plan = self.plan
        if plan is None:
            return
        if plan.restoring:
            if target == plan.restore:
                self.clear(f"已還原原本的上限 {target}%，排程結束")
            return
        changes = dict(applied=target)
        if raised:
            changes.update(raised_for=plan.deadline, raised_at=now)
        self.save(replace(plan, **changes))

    def write_skipped(self, target):
        """即將睡眠時來不及寫（使用者正在調整、另一個寫入或 10 秒的間隔）：不算失敗、不等 1 分鐘，
        喚醒後（或下一次判斷時）重新判斷。"""
        if self.pending != target:
            return
        self.pending, self.pending_raise = None, False
        self.log(f"即將睡眠，來不及把上限改成 {target}%，喚醒後再判斷")

    def take_notification(self):
        """這一輪該發的通知（NOTE_READY、NOTE_LATE 或 None），每一輪只發一次。"""
        plan, decision = self.plan, self.decision
        if plan is None or decision is None:
            return None
        if decision.phase == READY and plan.ready_notified_for != plan.deadline:
            self.save(replace(plan, ready_notified_for=plan.deadline))
            return NOTE_READY if plan.raised_for == plan.deadline else None
        if decision.phase == LATE and plan.late_notified_for != plan.deadline:
            self.save(replace(plan, late_notified_for=plan.deadline))
            return NOTE_LATE
        return None

    def begin_restore(self, plan, data):
        restoring = replace(plan, restoring=True)
        self.save(restoring)
        self.decision = None
        return self.restore(restoring, data)

    def restore(self, plan, data):
        if plan.restore == data.current_limit and self.pending is None:
            self.clear(f"上限已是原本的 {plan.restore}%，排程結束")
            return None
        return self.request(plan.restore, data)

    # ── 狀態 ───────────────────────────────────────────────

    def save(self, plan):
        self.plan = plan
        self.store.save(plan)

    def reset(self, plan):
        self.decision = self.pending = self.retry_at = self.write_error = None
        self.resume_until = None
        self.pending_raise = False
        self.save(plan)

    def clear(self, reason):
        self.log(reason)
        self.reset(None)
