"""智慧充電排程檔（charging-plan.json）的格式（純邏輯）。

{"format": 1, "plan": null | {"id", "schedule", "deadline", "target", "hold", "restore",
 "strategy", "created", "completed", "ready_notified_for", "late_notified_for", "restoring",
 "applied", "started_for", "raised_for", "raised_at"}}
schedule 為 {"kind": "once", "deadline": 秒} 或 {"kind": "weekly", "weekdays": [1–7], "minute": 0–1439}。
讀取時每個鍵與值的型別、範圍都必須相符，否則整個檔案視為無法解讀（由呼叫者備份）。
"""
import json
import math
import re

from sdm.constants import SmartCharging as Limits
from sdm.core.charging_plan import MINUTES_PER_DAY, STRATEGIES, WEEKDAYS, Once, Plan, Weekly

ID_PATTERN = re.compile(r"(?:[0-9a-f]{32})?")
PLAN_KEYS = frozenset({"id", "schedule", "deadline", "target", "hold", "restore", "strategy",
                       "created", "completed", "ready_notified_for", "late_notified_for",
                       "restoring", "applied", "started_for", "raised_for", "raised_at"})
OPTIONAL_TIMES = ("completed", "ready_notified_for", "late_notified_for", "started_for",
                  "raised_for", "raised_at")


class Undecodable(ValueError):
    """檔案無法解讀（不是 JSON、結構不對或值不合理）。"""


class NewerFormat(ValueError):
    """檔案來自較新版本，不能用目前的格式覆寫。"""


def is_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def is_percent(value):
    return type(value) is int and Limits.MIN_PERCENT <= value <= Limits.MAX_PERCENT


def schedule_value(schedule):
    if schedule.weekly:
        return {"kind": "weekly", "weekdays": sorted(schedule.weekdays), "minute": schedule.minute}
    return {"kind": "once", "deadline": schedule.deadline}


def plan_value(plan):
    value = {"id": plan.id, "schedule": schedule_value(plan.schedule), "deadline": plan.deadline,
             "target": plan.target, "hold": plan.hold, "restore": plan.restore,
             "strategy": plan.strategy, "created": plan.created, "restoring": plan.restoring,
             "applied": plan.applied}
    value.update({name: getattr(plan, name) for name in OPTIONAL_TIMES})
    return value


def encode_plan(plan):
    """排程（或 None：沒有排程）→ 檔案的位元組。"""
    value = {"format": Limits.PLAN_FORMAT, "plan": None if plan is None else plan_value(plan)}
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def decode_schedule(value):
    if not isinstance(value, dict):
        raise Undecodable("schedule")
    if value.get("kind") == "once" and set(value) == {"kind", "deadline"} \
            and is_time(value["deadline"]):
        return Once(float(value["deadline"]))
    if value.get("kind") == "weekly" and set(value) == {"kind", "weekdays", "minute"}:
        days, minute = value["weekdays"], value["minute"]
        if isinstance(days, list) and all(type(day) is int and day in WEEKDAYS for day in days) \
                and type(minute) is int and 0 <= minute < MINUTES_PER_DAY:
            return Weekly(frozenset(days), minute)
    raise Undecodable("schedule")


def check_plan(value):
    """鍵與值的型別、範圍；不符時拋出 Undecodable。"""
    if not isinstance(value, dict) or set(value) != PLAN_KEYS:
        raise Undecodable("keys")
    checks = (isinstance(value["id"], str) and ID_PATTERN.fullmatch(value["id"]),
              is_time(value["deadline"]), is_time(value["created"]),
              all(is_percent(value[name]) for name in ("target", "hold", "restore")),
              isinstance(value["strategy"], str) and value["strategy"] in STRATEGIES,
              type(value["restoring"]) is bool,
              value["applied"] is None or is_percent(value["applied"]),
              all(value[name] is None or is_time(value[name]) for name in OPTIONAL_TIMES))
    if not all(checks) or value["hold"] > value["target"]:
        raise Undecodable("values")


def decode_plan(data):
    """檔案的位元組 → Plan 或 None；無法解讀時拋出 Undecodable，較新的格式拋出 NewerFormat。"""
    try:
        value = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise Undecodable("json") from None
    if not isinstance(value, dict) or set(value) != {"format", "plan"} \
            or type(value["format"]) is not int or value["format"] < 1:
        raise Undecodable("structure")
    if value["format"] > Limits.PLAN_FORMAT:
        raise NewerFormat()
    plan = value["plan"]
    if plan is None:
        return None
    check_plan(plan)
    times = {name: None if plan[name] is None else float(plan[name]) for name in OPTIONAL_TIMES}
    return Plan(schedule=decode_schedule(plan["schedule"]), deadline=float(plan["deadline"]),
                target=plan["target"], hold=plan["hold"], restore=plan["restore"],
                strategy=plan["strategy"], created=float(plan["created"]), id=plan["id"],
                restoring=plan["restoring"], applied=plan["applied"], **times)
