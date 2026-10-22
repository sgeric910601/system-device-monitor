"""筆電「詳細資訊」的 18 個欄位（參考專案 DetailField 與 DetailsGridView），對應到 Linux：

- 欄位代號同時是設定 hidden_detail_fields 使用的名稱（小寫英文與底線）。
- 讀不到的欄位不顯示；使用者隱藏的欄位也不顯示（隱藏設定的介面在之後的階段加入）。
- 有充電上限時上限已顯示在時間卡片，詳細資訊不重複列出，並把低耗電模式移到最後，
  讓變壓器、變壓器電壓、變壓器電流排在一起（參考 DetailField.available）。
- Linux 沒有設計循環次數，循環次數只顯示數字；健康度與容量推算都來自滿充 ÷ 設計容量。
"""
from sdm.core.battery import CHARGING, DISCHARGING, round_half_up
from sdm.ui.battery_text import ESTIMATING, adapter_summary, limit_text
from sdm.ui.power_text import duration_text, watts_text

MILLI = 1000
MINUTES_PER_HOUR = 60
POWER_SAVER = "power-saver"
UNKNOWN = "Unknown"
DETAIL_FIELDS = (
    ("voltage", "電池電壓"), ("current", "電池電流"), ("battery_power", "電池功率"),
    ("system_power", "系統耗電"), ("system_estimate", "系統預估"), ("temperature", "電池溫度"),
    ("health", "健康度"), ("computed_health", "容量推算"), ("cycles", "循環次數"),
    ("condition", "電池狀況"), ("remaining", "剩餘容量"), ("full_charge", "滿充容量"),
    ("design", "設計容量"), ("low_power", "低耗電模式"), ("adapter", "變壓器"),
    ("charge_limit", "充電上限"), ("adapter_voltage", "變壓器電壓"),
    ("adapter_current", "變壓器電流"),
)
CONDITIONS = {"Good": "良好", "Overheat": "過熱", "Dead": "損壞", "Over voltage": "電壓過高",
              "Unspecified failure": "異常", "Cold": "過冷", "Over current": "電流過大",
              "Calibration required": "需要校正", "Warm": "偏熱", "Cool": "偏冷", "Hot": "過熱",
              "Watchdog timer expire": "保護計時器逾時", "Safety timer expire": "安全計時器逾時",
              "No battery": "沒有電池"}
LEVELS = {"Critical": "電量極低", "Low": "電量偏低", "Normal": "正常", "High": "電量充足",
          "Full": "已充飽"}


def available_fields(limit_shown_elsewhere):
    if not limit_shown_elsewhere:
        return DETAIL_FIELDS
    kept = tuple(field for field in DETAIL_FIELDS if field[0] not in ("charge_limit", "low_power"))
    return kept + tuple(field for field in DETAIL_FIELDS if field[0] == "low_power")


def milliamps(amps):
    return None if amps is None else f"{round_half_up(amps * MILLI):,} mA"


def clock(minutes):
    return f"{minutes // MINUTES_PER_HOUR}:{minutes % MINUTES_PER_HOUR:02d}"


def capacity(wh, ah):
    parts = []
    if wh is not None:
        parts.append(f"{wh:.1f} Wh")
    if ah is not None:
        parts.append(f"{round_half_up(ah * MILLI):,} mAh")
    if len(parts) == 2:
        return f"{parts[0]}（{parts[1]}）"
    return parts[0] if parts else None


def system_estimate(power):
    """UPower 的預估：用電池時是剩餘時間，充電時是到上限或充滿的時間（參考 systemEstimateText）。"""
    battery, e = power.battery, power.estimates
    if not e.system_reports:
        return None
    if e.state == DISCHARGING:
        return ESTIMATING if e.system_to_empty is None else duration_text(e.system_to_empty)
    if e.state == CHARGING:
        if battery.limit is not None:
            return ESTIMATING if e.to_limit is None else f"到 {battery.limit}% {clock(e.to_limit)}"
        return ESTIMATING if e.to_full is None else f"充滿 {clock(e.to_full)}"
    return "接電中"


def condition(battery):
    if battery.health and battery.health != UNKNOWN:
        return CONDITIONS.get(battery.health, battery.health)
    if battery.capacity_level and battery.capacity_level != UNKNOWN:
        return LEVELS.get(battery.capacity_level, battery.capacity_level)
    return None


def adapter(battery, value):
    """變壓器相關欄位：沒接電源時顯示「未連接」，接著但讀不到時不顯示。"""
    return value if battery.ac_online else "未連接"


def charge_limit(battery):
    return limit_text(battery) if battery.limit_supported else None


def battery_values(power):
    battery, e = power.battery, power.estimates
    health = e.health_pct
    return dict(
        voltage=None if battery.voltage_v is None else f"{battery.voltage_v:.2f} V",
        current=milliamps(battery.current_a),
        battery_power=None if e.battery_w is None else watts_text(abs(e.battery_w)),
        system_power=None if e.system_w is None else watts_text(e.system_w),
        system_estimate=system_estimate(power),
        temperature=None if battery.temperature_c is None else f"{battery.temperature_c:.1f} °C",
        health=None if health is None else f"{round_half_up(health)}%",
        computed_health=None if health is None else f"{health:.1f}%",
        cycles=None if battery.cycles is None else str(battery.cycles),
        condition=condition(battery),
        remaining=capacity(battery.energy_now_wh, battery.charge_now_ah),
        full_charge=capacity(battery.energy_full_wh, battery.charge_full_ah),
        design=capacity(battery.energy_design_wh, battery.charge_design_ah))


def negotiated(text):
    """變壓器電壓與電流來自 USB-C PD 協商的契約，不是量測值。"""
    return None if text is None else text + "（協商）"


def supply_values(power):
    battery = power.battery
    return dict(
        low_power=None if power.profile is None else (
            "開啟" if power.profile == POWER_SAVER else "關閉"),
        adapter=adapter(battery, adapter_summary(battery) or "已連接"),
        charge_limit=charge_limit(battery),
        adapter_voltage=adapter(battery, negotiated(None if battery.adapter_v is None
                                                    else f"{battery.adapter_v:.2f} V")),
        adapter_current=adapter(battery, negotiated(milliamps(battery.adapter_a))))


def detail_rows(power, hidden=()):
    """（代號、名稱、值）；依參考專案的順序，略過隱藏與讀不到的欄位。"""
    values = {**battery_values(power), **supply_values(power)}
    fields = available_fields(power.battery.limit is not None)
    hidden = set(hidden)
    return tuple((field_id, title, values[field_id]) for field_id, title in fields
                 if field_id not in hidden and values[field_id] is not None)
