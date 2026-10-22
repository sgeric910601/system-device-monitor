"""筆電模式的電源頁、流向圖、今日回顧與頂端列文字；不載入 GTK，方便測試。

文案與判斷沿用參考專案的 HeaderView、TimeCard、StaticPowerFlowView 與 DailyRecapView，
「macOS」改成「系統」；系統預估來自 UPower，沒有 UPower 時顯示「—」。
"""
from dataclasses import dataclass
import math

from sdm.constants import Battery
from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              UNIT_AMPS, round_half_up)
from sdm.core.fmt import PLACEHOLDER
from sdm.core.presentation import FLOW_ADAPTER, FLOW_CHARGING
from sdm.ui.power_text import duration_text, watts_text

TONE_NORMAL = "normal"
TONE_WARNING = "warning"
TONE_CRITICAL = "critical"
ESTIMATING = "計算中…"
APPROX = "約 "                # UPower 給不出時間、改用平均推算的約略值
BELOW_RESERVE = f"已低於 {Battery.RESERVE_PERCENT}%"
MILLI = 1000
MINUTES_PER_HOUR = 60
SECONDS_PER_MINUTE = 60
STATE_TITLES = {DISCHARGING: "放電中", CHARGING: "充電中", PLUGGED_IDLE: "接電・未充電",
                FULL: "已充飽"}


@dataclass(frozen=True)
class TimeCard:
    title: str
    message: str = ""        # 接電時的說明
    columns: tuple = ()      # 用電池時的表格表頭
    table: tuple = ()        # 用電池時的「到 0%／到 10%」兩列
    rows: tuple = ()         # （名稱、值）
    note: str = ""


@dataclass(frozen=True)
class BatteryFlowModel:
    """變壓器 → 系統 ⇄ 電池（參考 StaticPowerFlowView）。"""
    adapter_text: str
    adapter_active: bool
    system_text: str
    battery_title: str
    battery_text: str
    battery_active: bool
    battery_charging: bool    # True：系統 → 電池；False：電池 → 系統
    battery_icon: str


@dataclass(frozen=True)
class RecapText:
    headline: str
    metrics: tuple


def minutes_phrase(minutes):
    """「36 分鐘」「3 小時 25 分」「1 小時」。"""
    minutes = int(minutes)
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes} 分鐘"
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours} 小時" if rest == 0 else f"{hours} 小時 {rest} 分"


def percent_text(percent):
    return PLACEHOLDER if percent is None else f"{percent}%"


def state_text(power):
    if power.state == LIMIT_REACHED:
        return f"已達上限 {power.battery.limit}%"
    return STATE_TITLES.get(power.state, STATE_TITLES[PLUGGED_IDLE])


def remaining_phrase(minutes):
    return ESTIMATING if minutes is None else "還能用 " + minutes_phrase(minutes)


def charging_headline(power):
    battery, estimates = power.battery, power.estimates
    prefix = ""
    if battery.limit is not None and battery.level < battery.limit:
        minutes = estimates.to_limit
        suffix = f"後到 {battery.limit}%"
    else:
        minutes, suffix = estimates.to_full, "後充滿"
        prefix = APPROX if estimates.to_full_approx else ""
    return ESTIMATING if minutes is None else prefix + minutes_phrase(minutes) + suffix


def headline_text(power):
    """參考 HeaderView.headline：剩餘時間、充滿時間，或接電時的供電狀況。"""
    estimates, state = power.estimates, power.state
    if state == DISCHARGING:
        return remaining_phrase(estimates.headline_to_empty)
    if state == CHARGING:
        return charging_headline(power)
    if estimates.draining_on_ac:
        return "供電不足・" + remaining_phrase(estimates.headline_to_empty)
    if state == FULL:
        return "由變壓器供電"
    if state == LIMIT_REACHED and power.battery.draining_above_limit:
        return "放電到上限"
    return "暫停充電"


def tone(power):
    if power.estimates.draining_on_ac:
        return TONE_WARNING
    if power.state == DISCHARGING and power.percent is not None \
            and power.percent <= Battery.RESERVE_PERCENT:
        return TONE_CRITICAL
    return TONE_NORMAL


def header(power):
    """（大字電量、狀態、剩餘或充電時間、語氣）。"""
    return percent_text(power.percent), state_text(power), headline_text(power), tone(power)


def since_text(start, now):
    if start is None:
        return PLACEHOLDER
    return duration_text(max(0, int((now - start) // SECONDS_PER_MINUTE)))


def limit_text(battery):
    if battery.limit is None:
        return "未設定"
    if battery.limit_start is None:
        return f"{battery.limit}%"
    return f"{battery.limit}%（{battery.limit_start}% 以下才重新充電）"


def system_cell(minutes, reports, stalled=False):
    """UPower 的時間；還在估算為「計算中…」，超過 2 分鐘都給不出或沒有 UPower 為「—」。"""
    if minutes is not None:
        return duration_text(minutes)
    return ESTIMATING if reports and not stalled else PLACEHOLDER


def reserve_cell(power, minutes, system):
    """「到 10%」：已低於 10% 時直接說明；系統預估還在估算時顯示「計算中…」。"""
    if power.battery.level <= Battery.RESERVE_PERCENT:
        return BELOW_RESERVE
    if system:
        e = power.estimates
        return system_cell(minutes, e.system_reports, e.system_stalled)
    return duration_text(minutes)


def average_note(power):
    averages, voltage = power.estimates.averages, power.battery.voltage_v
    if averages.discharge is None:
        return ""
    if averages.unit != UNIT_AMPS:
        return f"近 3 分鐘平均放電 {watts_text(averages.discharge)}"
    text = f"近 3 分鐘平均放電 {round_half_up(averages.discharge * MILLI):,} mA"
    return text + (f"（約 {watts_text(averages.discharge * voltage)}）" if voltage else "")


def discharging_card(power, now, unplugged_at):
    e = power.estimates
    table = (("到 0%", system_cell(e.system_to_empty, e.system_reports, e.system_stalled),
              duration_text(e.live_to_empty)),
             (f"到 {Battery.RESERVE_PERCENT}%", reserve_cell(power, e.system_to_10, True),
              reserve_cell(power, e.live_to_10, False)))
    rows = (("已使用電池", since_text(unplugged_at, now)),)
    if power.battery.limit is not None:
        rows += (("充電上限", limit_text(power.battery)),)
    return TimeCard("預估可用時間", columns=("", "系統預估", "依近 3 分鐘平均"), table=table,
                    rows=rows, note=average_note(power))


def average_rate_text(power):
    averages = power.estimates.averages
    if averages.charge is None:
        return ESTIMATING
    if averages.unit == UNIT_AMPS:
        return f"{round_half_up(averages.charge * MILLI):,} mA"
    return watts_text(averages.charge)


def charging_card(power):
    battery, e = power.battery, power.estimates
    if battery.limit is not None:
        target = (f"充到上限（{battery.limit}%）", e.to_limit)
    else:
        target = ("充滿（100%）", e.to_full)
    approx = APPROX if e.to_full_approx and battery.limit is None else ""
    rows = ((target[0], ESTIMATING if target[1] is None else approx + duration_text(target[1])),
            ("近 3 分鐘平均充電", average_rate_text(power)),
            ("充入功率", watts_text(max(0.0, e.battery_w or 0.0))))
    return TimeCard("充電預估", rows=rows)


def drain_minutes(power):
    """電量高於上限、系統正在放電時，估計多久會降到上限（參考 minutesToDrain）。"""
    battery, averages = power.battery, power.estimates.averages
    if not battery.draining_above_limit:
        return None
    full = battery.energy_full_wh if averages.unit != UNIT_AMPS else battery.charge_full_ah
    rate = averages.discharge
    if full is None or rate is None or rate <= 0:
        return None
    share = (battery.level - battery.limit) / Battery.PERCENT
    return round_half_up(full * share / rate * MINUTES_PER_HOUR)


def plugged_message(power, now):
    battery, e = power.battery, power.estimates
    if power.state == LIMIT_REACHED:
        if battery.draining_above_limit:
            return f"電量高於上限，系統正在讓電池慢慢降到 {battery.limit}%。"
        if battery.level > battery.limit:
            return f"已超過充電上限，暫停充電；降到 {battery.limit}% 以下才會再充。"
        return "已充到上限，暫停充電，由變壓器供電。"
    if power.state == FULL:
        return "已充飽，由變壓器供電。"
    plugged = power.plugged_in_at
    if plugged is not None and now - plugged < Battery.JUST_PLUGGED_SECONDS:
        return "剛接上電源，幾秒後就會開始充電。"
    if e.draining_on_ac:
        return f"供電不足，電池仍在放電，預估剩餘 {duration_text(e.headline_to_empty)}。"
    return "接著電源但沒有在充電，可能是電池保護、溫度保護或充電門檻。"


def plugged_card(power, now):
    battery, rows = power.battery, ()
    if battery.limit is not None:
        rows += (("充電上限", limit_text(battery)),)
        minutes = drain_minutes(power)
        if minutes is not None:
            rows += (("降到上限約需", duration_text(minutes)),)
    summary = adapter_summary(battery)
    if summary is not None:
        rows += (("變壓器", summary),)
    return TimeCard("電源狀態", message=plugged_message(power, now), rows=rows)


def time_card(power, now, unplugged_at=None):
    """unplugged_at：快照沒有拔電時間時，改用歷史紀錄推算的時間。"""
    if power.state == DISCHARGING:
        start = power.unplugged_at if power.unplugged_at is not None else unplugged_at
        return discharging_card(power, now, start)
    if power.state == CHARGING:
        return charging_card(power)
    return plugged_card(power, now)


def adapter_summary(battery):
    """「協商 45 W／額定 65 W」；都讀不到時回傳 None。協商值是契約上限，不是實際輸入。"""
    parts = []
    if battery.adapter_negotiated_w is not None:
        parts.append(f"協商 {battery.adapter_negotiated_w:.0f} W")
    if battery.adapter_rated_w is not None:
        parts.append(f"額定 {battery.adapter_rated_w:.0f} W")
    return "／".join(parts) or None


def adapter_text(battery, estimates):
    """流向圖的變壓器節點：實際量到的瓦數，否則協商功率，再沒有就是「—」。"""
    if not battery.ac_online:
        return "未連接"
    if estimates.adapter_w is not None:
        return watts_text(estimates.adapter_w)
    if battery.adapter_negotiated_w is not None:
        return f"協商 {battery.adapter_negotiated_w:.0f} W"
    return PLACEHOLDER


def flow_model(power):
    battery, e = power.battery, power.estimates
    kind = e.flow_kind
    active, charging = kind != FLOW_ADAPTER, kind == FLOW_CHARGING
    watts = e.battery_w
    value = None if watts is None else (watts if kind in (FLOW_CHARGING, FLOW_ADAPTER) else -watts)
    title = "電池閒置" if not active else ("充入電池" if charging else "電池放電")
    return BatteryFlowModel(adapter_text(battery, e), battery.ac_online, watts_text(e.system_w),
                            title, watts_text(value), active, charging, tray_icon(power))


def recap_minutes(minutes):
    return f"{minutes} 分鐘" if minutes < MINUTES_PER_HOUR else minutes_phrase(minutes)


def recap_text(recap):
    net = recap.net_change
    headline = ("今天還沒有紀錄" if recap.start_percent is None
                else f"今天用掉 {recap.used_percent}%，補回 {recap.charged_percent}%")
    net_text = PLACEHOLDER if net is None else (f"+{net}%" if net > 0 else f"{net}%")
    return RecapText(headline, (
        ("用掉", f"{recap.used_percent}%"), ("補回", f"{recap.charged_percent}%"),
        ("淨變化", net_text), ("接電次數", f"{recap.plug_in_count} 次"),
        ("使用電池總時間", recap_minutes(recap.unplugged_minutes)),
        ("最長一次", recap_minutes(recap.longest_unplugged_minutes))))


def tray_label(power):
    return percent_text(power.percent) if power.percent is not None else "—%"


def tray_icon(power):
    """GNOME 圖示主題的電池圖示（與 GNOME Shell 相同的分級）；低於 10% 用主題的警示圖示。"""
    percent = power.percent
    if percent is None:
        return "battery-missing-symbolic"
    step = Battery.ICON_LEVEL_STEP
    level = step * math.floor(min(Battery.PERCENT, max(0, percent)) / step)
    charging = power.state == CHARGING
    if power.state == FULL or (charging and level == Battery.PERCENT):
        return "battery-level-100-charged-symbolic"
    suffix = "-charging" if charging else ""
    if level == 0:
        return f"battery-caution{suffix}-symbolic"
    return f"battery-level-{level}{suffix}-symbolic"


def menu_line(power):
    """頂端列選單第一行，例如「電池：82%・還能用 3 小時 25 分」。"""
    if power.state in (DISCHARGING, CHARGING) or power.estimates.draining_on_ac:
        detail = headline_text(power)
    else:
        detail = state_text(power)
    return f"電池：{tray_label(power)}・{detail}"
