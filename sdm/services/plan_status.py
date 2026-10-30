"""--charging-plan-status：以一般權限唯讀查詢智慧充電（不載入 GTK、不連 logind 或 UPower）。

讀取 XDG 資料目錄的 charging-plan.json、狀態目錄的 charging-plan.log 與電量歷史、
/sys/class/power_supply 的電池與門檻，印出排程、目前的階段、下一次提高上限與喚醒的時間，
以及紀錄檔最後 20 行。不寫任何檔案：排程檔損壞時只回報（看板下次開啟時才另存備份）。
沒有即時的平均充電速度，所需時間以最近 7 天的歷史速度（沒有時每小時 20%）估算，可能與看板略有不同；
上限以滑桿的預設選項取值（與剛開啟的看板相同；驅動拒絕過某些值時看板會縮減選項）。
"""
from datetime import datetime
import math
from pathlib import Path
import time

from sdm import paths
from sdm.constants import History, SmartCharging
from sdm.core.battery import CHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE, combine
from sdm.core.charging_plan import PRECISE_WAKE, PlanInput, charging_rate, evaluate
from sdm.core.charge_limit import DEFAULT_OPTIONS, normalized
from sdm.core.history import decode_history
from sdm.core.plan_format import NewerFormat, Undecodable, decode_plan
from sdm.data.charge_thresholds import find_battery
from sdm.data.power_supply import read_adapters, read_batteries
from sdm.services.plan_controller import PHASE_TITLES, STRATEGY_TITLES
from sdm.services.plan_store import PlanLog, PlanStore

HOLDING = (PLUGGED_IDLE, LIMIT_REACHED, FULL)
MAX_HISTORY_BYTES = History.MAX_FILE_BYTES


def iso(stamp, tz=None):
    return None if stamp is None else datetime.fromtimestamp(stamp, tz).astimezone(tz) \
        .isoformat(timespec="seconds")


def read_small(path, limit):
    try:
        if Path(path).stat().st_size > limit:
            return None
        return Path(path).read_bytes()
    except OSError:
        return None


def read_plan(path):
    """（排程或 None, 檔案狀態：ok／none／invalid／newer／unreadable）。"""
    try:
        data = PlanStore(path).read()       # 不跟隨符號連結、只讀一般檔案、最多 64 KB
    except FileNotFoundError:
        return None, "none"
    except OSError:
        return None, "unreadable"
    if data is None:
        return None, "invalid"
    try:
        plan = decode_plan(data)
    except NewerFormat:
        return None, "newer"
    except Undecodable:
        return None, "invalid"
    return plan, "ok" if plan is not None else "none"


def history_rate(path, now):
    data = read_small(path, MAX_HISTORY_BYTES)
    try:
        samples, _ = decode_history(data) if data else ((), 0)
    except ValueError:
        return None
    return charging_rate(samples, now)


def battery_state(root):
    """（電池快照或 None, 目前的上限或 None）。"""
    snapshot = combine(read_batteries(root), read_adapters(root))
    if snapshot is None or snapshot.percent is None:
        return None, None
    thresholds = find_battery(root)
    return snapshot, None if thresholds is None else normalized(thresholds.end)


def plan_value(plan, tz=None):
    schedule = plan.schedule
    value = {"kind": "weekly" if schedule.weekly else "once", "deadline": iso(plan.deadline, tz),
             "target": plan.target, "hold": plan.hold, "restore": plan.restore,
             "strategy": plan.strategy, "strategy_title": STRATEGY_TITLES[plan.strategy],
             "restoring": plan.restoring}
    if schedule.weekly:
        hours, minutes = divmod(schedule.minute, 60)
        value.update(weekdays=schedule.days(), time=f"{hours:02d}:{minutes:02d}")
    return value


def plan_input(snapshot, limit, rate, target, now):
    if snapshot is None:
        return PlanInput(now, 0, False, False, SmartCharging.MAX_PERCENT)
    estimate = None
    if rate and target > snapshot.percent:
        estimate = math.ceil((target - snapshot.percent) / rate * 60)
    state = snapshot.state
    return PlanInput(now, int(snapshot.percent), bool(snapshot.ac_online), limit is not None,
                     limit if limit is not None else SmartCharging.MAX_PERCENT, estimate,
                     state == CHARGING, bool(snapshot.ac_online) and state in HOLDING,
                     DEFAULT_OPTIONS)


def decision_value(plan, data, tz=None):
    decision = evaluate(plan, data)
    lead = SmartCharging.WAKE_LEAD_SECONDS if plan.strategy == PRECISE_WAKE else 0
    return {"phase": decision.phase, "phase_title": PHASE_TITLES[decision.phase],
            "deadline": iso(decision.deadline, tz),
            "next_raise": iso(decision.start_at - lead, tz) if decision.start_at - lead > data.now
            else None,
            "wake_at": iso(decision.wake_at, tz), "desired_limit": decision.desired_limit,
            "required_minutes": decision.required_minutes}


def build_status(plan_path, log_path, history_path, root=Path("/"), now=None, tz=None):
    now = time.time() if now is None else now
    plan, plan_file = read_plan(plan_path)
    snapshot, limit = battery_state(root)
    value = {"plan": None if plan is None else plan_value(plan, tz), "plan_file": plan_file,
             "battery": None if snapshot is None else {"percent": snapshot.percent,
                                                      "plugged": bool(snapshot.ac_online),
                                                      "limit": limit},
             "phase": None, "phase_title": None, "deadline": None, "next_raise": None,
             "wake_at": None, "desired_limit": None, "required_minutes": None,
             "log": PlanLog(log_path).tail(SmartCharging.LOG_TAIL_LINES)}
    if plan is not None:
        rate = history_rate(history_path, now)
        value.update(decision_value(plan, plan_input(snapshot, limit, rate, plan.target, now),
                                    tz))
    return value


def default_status():
    return build_status(paths.DATA/SmartCharging.PLAN_FILE, paths.STATE/SmartCharging.LOG_FILE,
                        paths.DATA/History.FILE_NAME)


def status_text(value):
    """給人看的摘要（每行一項）。"""
    if value["plan"] is None:
        return ["智慧充電：沒有排程" + ("（排程檔無法解讀）" if value["plan_file"] == "invalid"
                                 else "")]
    plan = value["plan"]
    lines = [f"智慧充電：{value['phase_title']}，{plan['deadline']} 前充到 {plan['target']}%"]
    if value["next_raise"]:
        lines.append(f"下一次提高上限：{value['next_raise']}")
    if value["wake_at"]:
        lines.append(f"精準喚醒：{value['wake_at']}")
    return lines
