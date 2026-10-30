"""看板的智慧充電（Monitor 的混入類別）：排程控制器、睡眠延遲、精準喚醒、無人授權與電源頁的那一列。

- 每份耗電快照（看不到視窗時也一樣）先交給充電上限跟著系統的值，再判斷一次；需要寫入時
  經由 ChargeControl.apply_automatically（從不帶 --persist），還原原本的上限時接受任何 50–100 的值。
- 只有筆電（有可調整的電池）才啟動；桌機不尋找元件、不連 logind、不執行任何程式。
- 有排程時才向 logind 要睡眠延遲鎖；即將睡眠時以 will_sleep 判斷（準備優先或喚醒沒排好就先
  提高到目標），寫完或 4 秒後放開。收到即將睡眠後，到喚醒後 3 秒的計時器真正執行為止都不做一般的
  判斷（計時器可能因為主迴圈忙而晚一點）：喚醒後「開機與喚醒後保留」的服務可能把上限改回使用者
  的值，3 秒後重新讀取門檻再以 did_wake 判斷，排程進行中以排程為準。關機前取消精準喚醒的鬧鐘（避免關機後在那個時間自動開機）。
- 使用者自己調整充電上限（放開滑桿或切換保留）時以使用者為準：結束排程、不還原，
  並取消排隊中的自動寫入。
- 「已準備好」與「可能趕不上」經由通知協調層送出，每一輪只送一次。
- 示範模式：示範排程只在記憶體、喚醒與授權只是假狀態、不連 logind；模擬測試：排程與紀錄只在
  記憶體，也不連 logind。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import pwd
import time
from typing import Callable, NamedTuple, Optional

from sdm.constants import SmartCharging
from sdm.core import alert_messages as messages
from sdm.core.charging_plan import LATE, PRECISE_WAKE, READINESS_FIRST, RateCache
from sdm.core.settings import PreferencesReadOnlyError
from sdm.services import plan_inputs
from sdm.services.plan_controller import (DID_WAKE, NOTE_READY, REFRESH, WILL_SLEEP,
                                          PlanController)
from sdm.services.plan_demo import demo_plan
from sdm.services.plan_store import MemoryLog, PlanLog, PlanStore
from sdm.services.preview import DESKTOP
from sdm.services.privileged import HelperRunner
from sdm.services.sleep_gate import SleepGate
from sdm.services.unattended_control import DemoUnattended, UnattendedControl
from sdm.services.wake_control import DemoWake, WakeControl
from sdm.ui import charging_plan_text as text

LOG = logging.getLogger(__name__)
DEMO_USER = "示範帳號"           # 示範模式的截圖不出現真正的帳號名稱
STRATEGY_KEY, WAKE_KEY = "charging_sleep_strategy", "charging_wake_enabled"


class PlanParts(NamedTuple):
    controller: object
    log: object
    wake: object
    unattended: object
    gate: object


@dataclass(frozen=True)
class PlanSnapshot:
    """電源頁那一列、編輯視窗與設定的「充電」顯示用的狀態（都在主執行緒產生）。"""
    plan: object = None
    decision: object = None
    now: float = 0.0
    reachable: Optional[int] = None     # 可能趕不上時，到時候最多能充到幾 %
    write_error: Optional[str] = None
    charge: object = None               # ChargeState
    wake: object = None                 # WakeState
    unattended: object = None           # UnattendedState
    original_limit: int = SmartCharging.MAX_PERCENT
    laptop: bool = False
    demo: bool = False
    user: str = ""
    strategy: str = READINESS_FIRST     # 設定中的睡眠策略（新排程使用）
    log_path: Optional[Path] = None
    percent: Optional[int] = None
    plugged: bool = False


class PlanActions(NamedTuple):
    snapshot: Callable[[], PlanSnapshot]
    start: Callable                     # start(排程) → 是否建立
    cancel: Callable[[], None]
    set_unattended: Callable            # set_unattended(開或關, done(是否成功))
    forecast: Callable                  # forecast(完成時間, 目標, 保養上限) → 文字
    open_log: Callable[[], None]


class ChargingPlanMixin:
    plan_controller = plan_log = plan_wake = plan_unattended = plan_gate = None
    plan_view = plan_editor = plan_rate = None
    plan_running = False
    plan_sleep_at = plan_resume_at = None
    plan_clock = staticmethod(time.time)
    plan_monotonic = staticmethod(time.monotonic)

    # ── 建立與啟動 ─────────────────────────────────────────

    def build_charging_plan(self):
        """建立排程與元件，把那一列放進電源頁；這時還不讀取、不執行任何程式。"""
        parts = self.make_plan_parts()
        self.plan_controller, self.plan_log, self.plan_wake = parts.controller, parts.log, \
            parts.wake
        self.plan_unattended, self.plan_gate = parts.unattended, parts.gate
        self.plan_rate = RateCache()
        self.plan_view = self.make_plan_view()
        if self.power_page is not None:
            self.power_page.set_charging_plan(self.plan_view)
        self.charge_control.user_commit = self.plan_user_commit

    def make_plan_parts(self):
        changed = self.plan_changed
        if self.preview:
            plan = demo_plan(self.preview, self.plan_clock(), self.charge_control.state.limit)
            store, log, gate = PlanStore.in_memory(plan), MemoryLog(), None
            wake, unattended = DemoWake(changed), DemoUnattended(changed)
        else:
            store = PlanStore.in_memory() if self.smoke else PlanStore()
            log = MemoryLog() if self.smoke else PlanLog()
            runner = HelperRunner()
            wake, unattended = WakeControl(runner, changed), UnattendedControl(runner, changed)
            gate = None if self.smoke else SleepGate(self.plan_bus, self.plan_will_sleep,
                                                     self.plan_did_wake, self.plan_shutdown,
                                                     timed_out=self.plan_sleep_timed_out)
        return PlanParts(PlanController(store, log=log.write), log, wake, unattended, gate)

    def make_plan_view(self):
        from sdm.ui.charging_plan_view import ChargingPlanView
        return ChargingPlanView(self.open_plan_editor)

    def plan_bus(self):
        return getattr(getattr(self, "collector", None), "bus", None)

    def start_plan_components(self):
        """只有筆電才尋找元件、查詢授權並訂閱 logind（不要求授權）。"""
        self.plan_running = self.charge_control.state.supply is not None \
            and self.preview != DESKTOP
        if not self.plan_running:
            self.plan_changed()
            return
        self.plan_wake.refresh()
        self.plan_unattended.refresh()
        if self.plan_gate is not None:
            self.plan_gate.start()
        plan = self.plan_controller.plan
        if plan is not None:
            self.plan_log.write("看板啟動，繼續排程（" + text.plan_summary(plan, self.plan_clock())
                                + "）")
        self.plan_tick()

    def stop_charging_plan(self):
        for part in (self.plan_gate, self.plan_wake, self.plan_unattended):
            if part is not None:
                part.stop()

    # ── 判斷與寫入 ─────────────────────────────────────────

    def plan_tick(self, event=REFRESH, done=None):
        """判斷一次；需要時寫入。done() 在判斷（與寫入）結束後呼叫。"""
        finish = done or (lambda: None)
        if not self.plan_running or self.closing or (event == REFRESH and self.plan_paused()):
            finish()
            return
        data = self.plan_input()
        if data is None:
            finish()
            return
        controller = self.plan_controller
        target = controller.evaluate(data, event, self.plan_wake.ready())
        self.plan_after()
        if target is None:
            finish()
            return
        exact = controller.plan is not None and controller.plan.restoring
        self.charge_control.apply_automatically(
            target, lambda ok, read_back: self.plan_written(target, ok, read_back, finish),
            exact=exact, urgent=event == WILL_SLEEP)

    def plan_input(self):
        power = self.current_power()
        self.charge_control.observe(power)
        state, plan, now = self.charge_control.state, self.plan_controller.plan, self.plan_clock()
        target = plan.target if plan is not None else SmartCharging.MAX_PERCENT
        return plan_inputs.plan_input(power, state.limit, state.supply is not None, target,
                                      self.plan_history_rate(now), now, state.options)

    def plan_history_rate(self, now):
        history = getattr(self, "history", None)
        return self.plan_rate.rate(lambda: history.samples if history is not None else (), now)

    def plan_written(self, target, ok, read_back, finish):
        """ok 為 None：即將睡眠時來不及寫（沒有嘗試）。"""
        if ok is None:
            self.plan_controller.write_skipped(target)
        else:
            self.plan_controller.write_finished(target, bool(ok) and read_back == target,
                                                read_back, self.plan_clock())
        self.plan_after()
        finish()

    def plan_after(self):
        """判斷或寫入之後：睡眠延遲鎖、精準喚醒、通知與畫面。"""
        if self.plan_gate is not None:
            self.plan_gate.set_active(self.plan_controller.active and self.plan_running)
        self.plan_sync_wake()
        self.plan_notify()
        self.plan_changed()

    def plan_sync_wake(self):
        plan, decision = self.plan_controller.plan, self.plan_controller.decision
        precise = plan is not None and plan.strategy == PRECISE_WAKE
        wake_at = decision.wake_at if precise and decision is not None else None
        self.plan_wake.sync(wake_at, precise, self.plan_unattended.state.granted is True)

    def plan_notify(self):
        note = self.plan_controller.take_notification()
        alerts = getattr(self, "alerts", None)
        plan = self.plan_controller.plan
        if note is None or alerts is None or plan is None:
            return
        if note == NOTE_READY:
            alert = messages.charging_ready(plan.target)
        else:
            alert = messages.charging_late(plan.target, self.plan_reachable(),
                                           text.clock_text(plan.deadline, self.plan_clock()))
        alerts.deliver((alert,))

    def plan_reachable(self):
        plan, decision = self.plan_controller.plan, self.plan_controller.decision
        if plan is None or decision is None or decision.phase != LATE:
            return None
        now = self.plan_clock()
        result = plan_inputs.make_forecast(self.current_power(), plan.target, plan.deadline, now,
                                           self.plan_history_rate(now))
        return None if result is None else result.reachable_percent

    # ── 睡眠、喚醒與關機 ───────────────────────────────────

    def plan_paused(self):
        """收到即將睡眠，到喚醒後的判斷（plan_resumed）真正執行為止；醒著超過 2 分鐘還沒收到
        喚醒或計時器還沒執行就不再等。"""
        now = self.plan_monotonic()
        for name in ("plan_sleep_at", "plan_resume_at"):
            since = getattr(self, name)
            if since is not None:
                if 0 <= now - since < SmartCharging.SLEEP_GUARD_SECONDS:
                    return True
                setattr(self, name, None)
        return False

    def plan_will_sleep(self, done):
        self.plan_sleep_at = self.plan_monotonic()
        if self.plan_controller.active:
            self.plan_log.write("系統即將睡眠")
        self.plan_tick(WILL_SLEEP, done)

    def plan_did_wake(self):
        self.plan_sleep_at = None
        if self.plan_controller.active:
            self.plan_log.write("系統已喚醒")
        self.plan_resume_at = self.plan_monotonic()
        self.schedule(SmartCharging.RESUME_SETTLE_SECONDS, self.plan_resumed)

    def plan_resumed(self):
        """喚醒 3 秒後：重新讀取門檻，以排程為準再判斷一次。"""
        self.plan_resume_at = None
        self.charge_control.reload()
        self.plan_tick(DID_WAKE)
        return False

    def plan_shutdown(self, done):
        self.plan_wake.clear_now(done)

    def plan_sleep_timed_out(self, kind):
        """延遲鎖是時間到才放開的（還沒做完）：記在紀錄檔，喚醒後再判斷。"""
        if self.plan_controller.active:
            self.plan_log.write("睡眠前 4 秒內沒有寫完，已讓系統先睡；喚醒後再判斷" if kind == "sleep"
                                else "關機前 4 秒內沒有取消完喚醒，鬧鐘可能仍在")

    # ── 使用者的操作 ───────────────────────────────────────

    def plan_user_commit(self, value):
        if not self.plan_controller.active:
            return
        self.plan_controller.user_changed_limit(value)
        self.charge_control.cancel_automatic()
        self.plan_after()

    def plan_original_limit(self):
        """排程開始前的上限：排程進行中沿用它記下的值，否則是保留的值或目前的上限。"""
        plan = self.plan_controller.plan
        if plan is not None:
            return plan.restore
        saved = self.charge_control.saved_limit()
        return saved.end if saved is not None else self.charge_control.state.limit

    def start_plan(self, schedule):
        """依設定的目標、保養上限與睡眠策略建立（或取代）排程；完成時間已過時回傳 False。"""
        if not self.plan_running:
            return False
        prefs, original = self.preferences, self.plan_original_limit()
        target = prefs.charging_target_percent or original
        started = self.plan_controller.start(schedule, target,
                                             min(prefs.charging_hold_percent, target), original,
                                             prefs.charging_sleep_strategy, self.plan_clock())
        if started:
            self.plan_tick()
        else:
            self.plan_changed()
        return started

    def cancel_plan(self):
        """取消排程並還原原本的上限（寫入失敗時保留，1 分鐘後重試）。"""
        data = self.plan_input() if self.plan_running else None
        if data is None:
            return
        target = self.plan_controller.cancel(data)
        self.plan_after()
        if target is not None:
            self.charge_control.apply_automatically(
                target, lambda ok, read_back: self.plan_written(target, ok, read_back,
                                                                lambda: None), exact=True)

    def plan_preferences_changed(self, keys):
        """設定的睡眠策略改變：目前的排程也改用新的策略（精準喚醒的選項跟著改）。"""
        if STRATEGY_KEY not in keys or self.plan_controller is None:
            return
        strategy = self.preferences.charging_sleep_strategy
        self.plan_controller.set_strategy(strategy)
        wanted = strategy == PRECISE_WAKE
        if self.preferences.charging_wake_enabled != wanted:
            try:
                self.store.update(**{WAKE_KEY: wanted})
            except (PreferencesReadOnlyError, KeyError, ValueError) as error:
                LOG.warning("Cannot save the wake preference: %s", type(error).__name__)
        self.plan_tick()

    def set_unattended(self, wanted, done):
        """開關「允許無人時自動調整」；關閉前先取消已排定的喚醒（仍免密碼時）。"""
        def finished(ok):
            self.plan_after()
            done(ok)

        if not wanted and self.plan_wake.state.scheduled is not None:
            self.plan_wake.clear_now(lambda: self.plan_unattended.set_granted(False, finished))
            return
        self.plan_unattended.set_granted(wanted, finished)

    # ── 顯示 ───────────────────────────────────────────────

    def plan_user(self):
        if self.preview:
            return DEMO_USER
        try:
            return pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            return ""

    def plan_snapshot(self):
        controller, charge = self.plan_controller, self.charge_control
        battery = getattr(self.current_power(), "battery", None)
        return PlanSnapshot(
            plan=controller.plan, decision=controller.decision, now=self.plan_clock(),
            reachable=self.plan_reachable(), write_error=controller.write_error,
            charge=charge.state, wake=self.plan_wake.state, unattended=self.plan_unattended.state,
            original_limit=self.plan_original_limit(), laptop=self.plan_running,
            demo=bool(self.preview), user=self.plan_user(),
            strategy=self.preferences.charging_sleep_strategy,
            log_path=getattr(self.plan_log, "path", None),
            percent=None if battery is None else battery.percent,
            plugged=battery is not None and bool(battery.ac_online))

    def plan_forecast_text(self, deadline, target, hold):
        power, now = self.current_power(), self.plan_clock()
        result = plan_inputs.make_forecast(power, target, deadline, now,
                                           self.plan_history_rate(now))
        if result is None:
            return ""
        battery = power.battery
        return text.forecast_text(int(battery.percent), target, hold, result, deadline, now,
                                  None, bool(battery.ac_online))

    def plan_actions(self):
        return PlanActions(self.plan_snapshot, self.start_plan, self.cancel_plan,
                           self.set_unattended, self.plan_forecast_text, self.open_plan_log)

    def plan_changed(self):
        """排程、喚醒或授權的狀態改變：更新那一列、編輯視窗與設定的「充電」。"""
        if self.plan_view is None or self.plan_controller is None:
            return
        snapshot = self.plan_snapshot()
        self.plan_view.sync(snapshot)
        if self.plan_editor is not None:
            self.plan_editor.sync(snapshot)
        window = getattr(self, "settings_window", None)
        if window is not None and hasattr(window, "charging_changed"):
            window.charging_changed()

    def open_plan_editor(self):
        if self.plan_editor is None:
            from sdm.ui.charging_plan_dialog import ChargingPlanDialog
            self.plan_editor = ChargingPlanDialog(self.window, self.store, self.plan_actions())
        self.plan_editor.present(self.plan_snapshot())

    def open_plan_log(self):
        """以預設的程式開啟紀錄檔（示範模式與模擬測試沒有檔案）。"""
        path = getattr(self.plan_log, "path", None)
        if path is None or not Path(path).is_file():
            return
        from gi.repository import Gio, GLib
        try:
            Gio.AppInfo.launch_default_for_uri(Path(path).as_uri(), None)
        except GLib.Error as error:
            LOG.warning("Cannot open the charging plan log: %s", error.domain)
