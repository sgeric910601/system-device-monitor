"""智慧充電的排程與判斷（純邏輯，移植參考專案 Model/ChargingPlan.swift）。

使用者指定「幾點前充到幾 %」：平常把充電上限維持在保養上限，算好需要的時間再把上限提高到
目標，讓系統自己充；結束後還原原本的上限。這裡只有排程與判斷，不讀寫系統。
- 時間一律是 Unix 時間（秒）；每週排程的星期用 ISO 編號（1＝星期一 … 7＝星期日），
  時區用 zoneinfo（tz 為 None 時用本機時區，含日光節約）。
- 日光節約跳過的時刻改用下一個存在的時間（保留分鐘），重複的時刻用第一次
  （與參考專案的 nextTimePreservingSmallerComponents、repeatedTimePolicy .first 相同）。
- 參考專案中只適用 macOS 的部分不移植：macOS 的充電上限服務（PowerUI）、
  IOPMSchedulePowerEvent 的喚醒排程與暗喚醒（dark wake），Linux 改用充電上限小程式、
  RTC 喚醒小程式與 logind（見 sdm.services 的對應模組）。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Optional

from sdm.constants import SmartCharging as Limits

READINESS_FIRST, PRECISE_WAKE = "readiness_first", "precise_wake"
STRATEGIES = (READINESS_FIRST, PRECISE_WAKE)
UNSUPPORTED, UNPLUGGED, WAITING, STARTING = "unsupported", "unplugged", "waiting", "starting"
CHARGING, SYSTEM_PAUSED, READY, LATE = "charging", "system_paused", "ready", "late"
PHASES = (UNSUPPORTED, UNPLUGGED, WAITING, STARTING, CHARGING, SYSTEM_PAUSED, READY, LATE)
MINUTES_PER_DAY = 24 * 60
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
WEEKDAYS = range(1, 8)
DAYS_AHEAD = 8                  # 今天到下週同一天
GAP_SHIFTS = 3                  # 跳過的時刻最多往後試 2 小時


def is_integer(value):
    return type(value) is int


def exists(moment):
    """這個本機時刻真的存在（沒有被日光節約跳過）。"""
    back = datetime.fromtimestamp(moment.timestamp(), moment.tzinfo)
    return back.replace(tzinfo=None, fold=0) == moment.replace(tzinfo=None, fold=0)


def wall_time(day, minute, tz):
    """day 那天的第 minute 分鐘（本機時間）的 Unix 時間。"""
    hour, rest = divmod(minute, MINUTES_PER_HOUR)
    naive = datetime(day.year, day.month, day.day, hour, rest)
    for shift in range(GAP_SHIFTS):
        moment = (naive + timedelta(hours=shift)).replace(tzinfo=tz, fold=0)
        if exists(moment):
            return moment.timestamp()
    return naive.replace(tzinfo=tz, fold=0).timestamp()


@dataclass(frozen=True)
class Once:
    """一次性：指定完成時間。"""
    deadline: float
    weekly = False

    def next_deadline(self, after, tz=None):
        return self.deadline if self.deadline > after else None


@dataclass(frozen=True)
class Weekly:
    """每週：weekdays 為 ISO 星期（1–7），minute 是當天的第幾分鐘。"""
    weekdays: frozenset
    minute: int
    weekly = True

    def days(self):
        return sorted(day for day in self.weekdays if is_integer(day) and day in WEEKDAYS)

    def next_deadline(self, after, tz=None):
        """after 之後（不含）最近的一次；星期或分鐘不合理時為 None。"""
        days = self.days()
        if not is_integer(self.minute) or not 0 <= self.minute < MINUTES_PER_DAY or not days:
            return None
        today = datetime.fromtimestamp(after, tz).date()
        stamps = [wall_time(today + timedelta(days=offset), self.minute, tz)
                  for offset in range(DAYS_AHEAD)
                  if (today + timedelta(days=offset)).isoweekday() in days]
        later = [stamp for stamp in stamps if stamp > after]
        return min(later) if later else None


@dataclass(frozen=True)
class Plan:
    """一個排程。deadline 是這一輪的完成時間（每週排程完成或過期後換到下一次）。"""
    schedule: object
    deadline: float
    target: int                 # 完成時要充到的電量
    hold: int                   # 等待期間維持的充電上限（保養上限）
    restore: int                # 建立排程前使用者原本的上限，結束時還原
    strategy: str
    created: float
    id: str = ""
    completed: Optional[float] = None           # 這一輪第一次達到目標的時間
    ready_notified_for: Optional[float] = None  # 已通知「已準備好」的那一輪（重開程式不重複）
    late_notified_for: Optional[float] = None
    restoring: bool = False                     # 正在還原原本的上限（失敗時保留，稍後重試）
    applied: Optional[int] = None               # 排程最後一次成功寫入的上限
    started_for: Optional[float] = None         # 這一輪已經開始充電：之後不回到等待
    raised_for: Optional[float] = None          # 這一輪曾把上限提高到目標：有提高過才通知
    raised_at: Optional[float] = None           # 最近一次提高到目標的時間


def clamp(percent):
    """充電上限只能設 50–100%。"""
    return min(max(int(percent), Limits.MIN_PERCENT), Limits.MAX_PERCENT)


def make_plan(schedule, deadline, target, hold, restore, strategy, created, plan_id=""):
    target = clamp(target)
    return Plan(schedule, deadline, target, min(clamp(hold), target), clamp(restore), strategy,
                created, plan_id)


@dataclass(frozen=True)
class PlanInput:
    """判斷時需要的即時狀態；estimated_minutes 為充到目標還要幾分鐘（沒有時為 None）。"""
    now: float
    percent: int
    connected: bool
    limit_supported: bool
    current_limit: int
    estimated_minutes: Optional[int] = None
    charging: bool = False
    paused: bool = False        # 接著電源但沒在充（到達上限、韌體暫停等）


@dataclass(frozen=True)
class Decision:
    phase: str
    target: int
    deadline: float
    start_at: float             # 預計開始充電的時間
    wake_at: Optional[float]    # 精準喚醒要排的時間（開始前一分鐘）；只有等待時才有
    desired_limit: Optional[int]    # 現在應該設定的上限；None 表示不動
    required_minutes: int


def hold_limit(plan, percent):
    """等待時的上限：保養上限，但不低於目前電量（進位到 5% 一格，不超過完成目標）。
    電量已高於保養上限時把上限降下去，電池會先放電再充回來，白白多一次充放電。"""
    step = Limits.HOLD_STEP
    current = math.ceil(max(0, percent) / step) * step
    return min(plan.target, max(plan.hold, current))


def required_minutes(plan, data):
    """充到目標需要的分鐘數：有預估用預估，沒有就以每小時 20% 估算；
    80% 以上的部分乘上 1.8（運算順序與參考專案相同，浮點結果一致）。"""
    percent = max(0, data.percent)
    if percent >= plan.target:
        return 0
    estimate = data.estimated_minutes
    if estimate is not None and estimate > 0:
        linear = float(estimate)
    else:
        linear = (plan.target - percent) / Limits.FALLBACK_RATE * MINUTES_PER_HOUR
    slow_share = max(0, plan.target - max(percent, Limits.TAPER_START)) / (plan.target - percent)
    minutes = math.ceil(linear * (1 + (Limits.TAPER_FACTOR - 1) * slow_share))
    return min(minutes, Limits.MAXIMUM_MINUTES)


def evaluate(plan, data):
    """依排程與即時狀態決定階段與要設定的上限。"""
    needed = required_minutes(plan, data)
    start = plan.deadline - (needed + Limits.BUFFER_MINUTES) * SECONDS_PER_MINUTE
    precise = plan.strategy == PRECISE_WAKE
    # 精準喚醒排在開始前一分鐘，醒來就提高上限：被叫醒的電腦可能很快又睡回去，等不到開始時間
    raise_at = start - Limits.WAKE_LEAD_SECONDS if precise else start
    wake = raise_at if precise and raise_at > data.now else None

    def decide(phase, limit):
        # 只有還在等待時才需要叫醒：已經充好、沒接電源或已經開始充，叫醒也沒事做
        return Decision(phase, plan.target, plan.deadline, start,
                        wake if phase == WAITING else None, limit, needed)

    if not data.limit_supported:
        return decide(UNSUPPORTED, None)
    if not data.connected:
        return decide(UNPLUGGED, None)
    if data.percent >= plan.target:
        return decide(READY, plan.target)
    if data.now >= plan.deadline:
        return decide(LATE, plan.target)
    if data.now < raise_at and plan.started_for != plan.deadline:
        return decide(WAITING, hold_limit(plan, data.percent))
    if data.now + needed * SECONDS_PER_MINUTE > plan.deadline:
        return decide(LATE, plan.target)
    if data.charging:
        return decide(CHARGING, plan.target)
    # 剛提高上限時可能還沒開始充：上限還沒是目標就是「準備充電」，已經是目標卻沒在充才算系統暫停
    if data.paused and data.current_limit == plan.target:
        return decide(SYSTEM_PAUSED, plan.target)
    return decide(STARTING, plan.target)


@dataclass(frozen=True)
class Forecast:
    needed_minutes: int         # 從目前電量充到目標需要的分鐘數（含 80% 以上變慢的部分）
    on_time: bool
    reachable_percent: int      # 到時候最多能充到幾 %


def forecast(percent, target, minutes_available, percent_per_hour):
    """建立排程前的預估；percent_per_hour 是 80% 以下的充電速度。"""
    rate = max(1, percent_per_hour)
    slow_rate = rate / Limits.TAPER_FACTOR
    knee = Limits.TAPER_START
    start = min(max(0, percent), Limits.MAX_PERCENT)
    fast_part = max(0, min(target, knee) - start)
    slow_part = max(0, target - max(start, knee))
    needed = math.ceil(fast_part / rate * MINUTES_PER_HOUR
                       + slow_part / slow_rate * MINUTES_PER_HOUR)
    minutes = max(0, minutes_available)
    minutes_to_knee = max(0, knee - start) / rate * MINUTES_PER_HOUR
    if minutes <= minutes_to_knee:
        reachable = start + rate * minutes / MINUTES_PER_HOUR
    else:
        reachable = max(start, knee) + slow_rate * (minutes - minutes_to_knee) / MINUTES_PER_HOUR
    return Forecast(needed, needed <= minutes,
                    min(Limits.MAX_PERCENT, math.floor(reachable)))


def charging_sessions(samples, now):
    """最近 7 天每一段連續充電的（總共充了幾 %, 總秒數）。"""
    rows = sorted((sample for sample in samples
                   if now - Limits.RATE_WINDOW_SECONDS <= sample.t <= now
                   and sample.pct is not None and sample.pct >= 0), key=lambda item: item.t)
    sessions, gained, seconds = [], 0, 0.0
    for left, right in zip(rows, rows[1:]):
        dt = right.t - left.t
        # 中間斷了很久（睡眠、看板沒開）或沒在充電，這一段結束
        if not (0 < dt <= Limits.RATE_GAP_SECONDS and left.plugged and right.plugged
                and left.charging):
            sessions.append((gained, seconds))
            gained, seconds = 0, 0.0
            continue
        gained += max(0, right.pct - left.pct)
        seconds += dt
    sessions.append((gained, seconds))
    return sessions


def charging_rate(samples, now):
    """最近七天實際的充電速度（每小時 %）；沒有足夠的資料時為 None。

    電量歷史每分鐘一筆、電量是整數，大部分相鄰兩筆的電量相同，所以不能只看電量有上升的
    那幾筆：以每一段連續充電「總共充了幾 % ÷ 總共花了多久」計算，再取各段的中位數。"""
    rates = sorted(gained / seconds * 3600 for gained, seconds in charging_sessions(samples, now)
                   if seconds >= Limits.RATE_MIN_SECONDS and gained >= Limits.RATE_MIN_PERCENT)
    if not rates:
        return None
    middle = len(rates) // 2
    rate = rates[middle] if len(rates) % 2 else (rates[middle - 1] + rates[middle]) / 2
    return rate if math.isfinite(rate) and Limits.RATE_LOW <= rate <= Limits.RATE_HIGH else None


class RateCache:
    """歷史充電速度：10 分鐘內不重算；samples() 只在需要重算時呼叫。"""

    def __init__(self):
        self.value, self.at = None, None

    def rate(self, samples, now):
        if self.at is not None and abs(now - self.at) < Limits.RATE_CACHE_SECONDS:
            return self.value
        self.value, self.at = charging_rate(samples(), now), now
        return self.value


def is_real(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def wake_allowed(epoch, now):
    """喚醒時間只能在 60 秒後到 7 天內（與喚醒小程式的檢查相同）。"""
    if not is_real(epoch) or not math.isfinite(epoch):
        return False
    return Limits.WAKE_EARLIEST <= epoch - now <= Limits.WAKE_LATEST


def wake_should_replace(existing, candidate):
    """已經排定的喚醒與新的時間相差 5 分鐘以上才重排。"""
    return existing is None or abs(candidate - existing) >= Limits.WAKE_REPLACE_SECONDS
