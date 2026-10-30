"""智慧充電的文字（不載入 GTK）：電源頁的狀態列、排程摘要、建立前的預估、睡眠策略、
驅動不接受要求的上限時的說明、精準喚醒與「允許無人時自動調整」的說明。時間格式沿用參考專案 ChargingJourneyPresentation：
今天只寫時刻、明天寫「明天 05:57」、更晚寫「10/16 07:30」。"""
from datetime import datetime

from sdm.constants import SmartCharging, Wake
from sdm.core.charging_plan import (CHARGING, LATE, PRECISE_WAKE, READINESS_FIRST, READY,
                                    STARTING, SYSTEM_PAUSED, UNPLUGGED, UNSUPPORTED, WAITING,
                                    fit_limit, fitted, options_text)
from sdm.services import wake_control

TITLE = "智慧充電"
NO_PLAN = "未排程"
JUDGING = "判斷中…"
HINT = "在指定的時間前充好，平常維持保養上限。"
SETTINGS_BUTTON = "設定…"
TONE_MUTED, TONE_WARNING, TONE_GOOD = "muted", "warning", "good"
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
WEEKDAY_NAMES = ("一", "二", "三", "四", "五", "六", "日")      # ISO 1–7
STRATEGY_TITLES = {READINESS_FIRST: "準備優先", PRECISE_WAKE: "精準喚醒（需要喚醒元件）"}
STRATEGY_NOTES = {
    READINESS_FIRST: "睡著前先提高上限，在睡眠中充好，可能會比預定時間早充滿。",
    PRECISE_WAKE: ("在該開始充電前一分鐘叫醒電腦提高上限；需要喚醒元件與「允許無人時自動調整」，"
                   "做不到時睡眠前改用準備優先。"),
}
ORIGINAL_LIMIT = "原本的上限（{}%）"
UNPLUGGED_PREFIX = "接上電源的話，"
WAKE_FALLBACK = "，睡眠前會改用準備優先"
WAKE_ERRORS = {
    "rtc-offset": "硬體時鐘不是 UTC（例如與 Windows 雙系統），無法精準喚醒",
    "alarm-in-use": "另一個程式已經設定了喚醒時間，沒有更改",
    "no-rtc": "這台電腦沒有可用的 RTC 喚醒",
    "out-of-range": "喚醒時間超出允許的範圍（60 秒後到 7 天內）",
    Wake.HOURLY_LIMIT: f"這一小時內已設定喚醒時間 {Wake.HOURLY_SETS} 次，先暫停",
    wake_control.UNAUTHORIZED: "未授權",
    wake_control.TIMEOUT: "喚醒元件超過 2 分鐘沒有回應",
}


def clock_text(stamp, now, tz=None):
    moment, today = datetime.fromtimestamp(stamp, tz), datetime.fromtimestamp(now, tz)
    days = (moment.date() - today.date()).days
    clock = moment.strftime("%H:%M")
    if days == 0:
        return clock
    if days == 1:
        return "明天 " + clock
    return f"{moment.month}/{moment.day} {clock}"


def duration_text(minutes):
    if minutes < MINUTES_PER_HOUR:
        return f"{max(1, minutes)} 分鐘"
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours} 小時" if rest == 0 else f"{hours} 小時 {rest} 分"


def status_line(plan, decision, now, tz=None, reachable=None):
    """（文字、色調）：電源頁「智慧充電」那一列的狀態。"""
    if plan is None:
        return NO_PLAN, TONE_MUTED
    if plan.restoring:
        return f"還原中・改回 {plan.restore}%", TONE_MUTED
    if decision is None:
        return JUDGING, TONE_MUTED
    phase, target = decision.phase, decision.target
    deadline = clock_text(decision.deadline, now, tz)
    if phase == WAITING:
        return f"等待開始・{clock_text(decision.start_at, now, tz)} 開始", TONE_MUTED
    if phase == CHARGING:
        done = now + decision.required_minutes * SECONDS_PER_MINUTE
        return f"充電中・預計 {clock_text(done, now, tz)} 到 {target}%", TONE_GOOD
    if phase == LATE:
        return ("可能趕不上" if reachable is None else f"可能趕不上：只能充到 {reachable}%",
                TONE_WARNING)
    fixed = {READY: ("已準備好", TONE_GOOD), SYSTEM_PAUSED: ("暫停（系統）", TONE_WARNING),
             STARTING: (f"準備充電・{deadline} 前充到 {target}%", TONE_MUTED),
             UNPLUGGED: (f"等待接上電源・{deadline} 前充到 {target}%", TONE_MUTED),
             UNSUPPORTED: ("這顆電池無法調整充電上限", TONE_WARNING)}
    return fixed[phase]


def weekdays_text(schedule):
    return "、".join(WEEKDAY_NAMES[day - 1] for day in schedule.days())


def plan_summary(plan, now, tz=None):
    """「明天 08:00 前充到 100%・等待時維持 80%・結束後還原為 90%」。"""
    schedule = plan.schedule
    if schedule.weekly:
        hours, minutes = divmod(schedule.minute, MINUTES_PER_HOUR)
        when = f"每週{weekdays_text(schedule)} {hours:02d}:{minutes:02d}"
    else:
        when = clock_text(plan.deadline, now, tz)
    if plan.target == plan.restore:
        return f"{when} 前充到 {plan.target}%（原本的上限）・等待時維持 {plan.hold}%"
    return (f"{when} 前充到 {plan.target}%・等待時維持 {plan.hold}%"
            f"・結束後還原為 {plan.restore}%")


def fitted_note(target, hold, options):
    """驅動不接受要求的上限時的說明（「驅動只支援 80% 與 100%，保養上限實際為 80%」）；
    都接受或不限制時為空字串。"""
    if not options:
        return ""
    top, kept = fitted(target, hold, options)
    parts = [f"保養上限實際為 {kept}%" if kept != min(hold, target) else "",
             f"提高時的上限實際為 {top}%" if top != target else ""]
    parts = [part for part in parts if part]
    return f"驅動只支援 {options_text(options)}，" + "、".join(parts) if parts else ""


def unsupported_hold(hold, options):
    """設定裡的保養上限已不在選項中（驅動拒絕過、選項變少）時的提醒；支援或不限制時為空字串。"""
    if not options or hold in options:
        return ""
    return (f"驅動只支援 {options_text(options)}，設定的保養上限 {hold}% "
            f"會改用 {fit_limit(hold, options, False)}%")


def hold_choices(saved, options):
    """保養上限選單的（值、名稱）：驅動接受的選項，另外一定包含設定裡的值（不在選項中時寫出實際
    會用的值，例如「85%（實際 80%）」），選單不會是空的。"""
    def name(value):
        if not options or value in options:
            return f"{value}%"
        return f"{value}%（實際 {fit_limit(value, options, False)}%）"
    return tuple((value, name(value)) for value in sorted(set(options) | {saved}))


def plan_fitted_note(plan, charge):
    """目前排程的說明；沒有排程或不知道驅動接受哪些值時為空字串。"""
    if plan is None or charge is None:
        return ""
    return fitted_note(plan.target, plan.hold, charge.options)


def cancel_label(plan):
    return f"取消排程並還原為 {plan.restore}%"


def forecast_text(percent, target, hold, forecast, deadline, now, tz=None, plugged=True):
    """建立排程前的預估：要多久、何時開始，或來不及時最多能充到幾 %。"""
    if forecast is None:
        return ""
    prefix = "" if plugged else UNPLUGGED_PREFIX
    needed = duration_text(forecast.needed_minutes)
    if percent >= target:
        return f"已達 {target}%"
    if not forecast.on_time:
        return f"{prefix}來不及：最多約 {forecast.reachable_percent}%（需要約 {needed}）"
    if min(hold, target) >= target:
        return f"{prefix}會直接充到 {target}%（約需 {needed}）"
    start = deadline - (forecast.needed_minutes + SmartCharging.BUFFER_MINUTES) * SECONDS_PER_MINUTE
    if start - now > SECONDS_PER_MINUTE:
        return f"{prefix}需要約 {needed}，會在{spaced(clock_text(start, now, tz))}開始"
    return f"{prefix}需要約 {needed}，會立刻開始充"


def spaced(clock):
    """中文與時刻之間的空白：「會在 05:57 開始」「會在明天 05:57 開始」。"""
    return (" " + clock if clock[:1].isdigit() else clock) + " "


def unattended_scope(user):
    """用一般的話寫出授權的範圍。"""
    return (f"只限你的帳號（{user}）、在這台電腦登入而且正在使用時；只允許調整充電上限"
            "（50–100%）與設定或取消智慧充電的喚醒時間，不包含其他任何管理員動作。"
            "沒有允許時，排程在無人時（例如半夜）需要的調整會因為等不到密碼而失敗。"
            "可以隨時關閉（開啟與關閉都需要管理員密碼）。")


def unattended_status(state):
    if state.busy:
        return "等待管理員授權…"
    if state.granted is None:
        return "確認中…"
    if not state.ready:
        return "需要先安裝管理員元件（含無人授權開關，見「設定 → 管理員功能」）"
    return "已允許" if state.granted else "未允許（需要時會要求管理員密碼）"


def wake_note(state, strategy, now=None, tz=None):
    """精準喚醒的狀態；準備優先時不顯示。"""
    if strategy != PRECISE_WAKE:
        return ""
    note = state.note
    if note == wake_control.SCHEDULED and state.scheduled is not None and now is not None:
        clock = clock_text(state.scheduled, now, tz)
        return f"已排定{'今天 ' + clock if clock[:1].isdigit() else clock} 喚醒"
    if note == wake_control.MISSING:
        return "精準喚醒需要喚醒元件" + WAKE_FALLBACK
    if note == wake_control.NOT_GRANTED:
        return "精準喚醒需要先「允許無人時自動調整」" + WAKE_FALLBACK
    if note == wake_control.FAILED:
        return WAKE_ERRORS.get(state.error, "無法設定喚醒時間") + WAKE_FALLBACK
    if note == wake_control.WAITING:
        return "正在設定喚醒時間…"
    return "已允許，目前不需要喚醒" if note == wake_control.IDLE else ""
