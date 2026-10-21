"""電源頁、頂端列與選單的文字；不載入 GTK，方便測試。

整機耗電在標題與頂端列取整數（不斷電系統負載本來就以約 9 W 為一格），
拆解清單與流向圖保留一位小數。
"""
from dataclasses import dataclass
from typing import Optional

from sdm.core.fmt import PLACEHOLDER, percent
from sdm.core.power_breakdown import SOURCE_COMPONENTS, SOURCE_NONE, SOURCE_UPS
from sdm.services.power_service import supply_label

TONE_NORMAL = "normal"
TONE_WARNING = "warning"
TONE_CRITICAL = "critical"
UPS_NOTE = "不斷電系統輸出（含螢幕等所有插在上面的設備）"
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class FlowModel:
    """流向圖要畫的內容；內容不變時不重畫。"""
    source_label: str
    total_label: str
    total_w: float
    items: tuple


def watts_short(value):
    return PLACEHOLDER if value is None else f"{value:.0f} W"


def watts_text(value):
    return PLACEHOLDER if value is None else f"{value:.1f} W"


def duration_text(minutes):
    """參考專案 Format.duration：「44 分」「2 小時 5 分」。"""
    if minutes is None or minutes < 0:
        return PLACEHOLDER
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} 分"
    hours, rest = divmod(minutes, 60)
    return f"{hours} 小時" if rest == 0 else f"{hours} 小時 {rest} 分"


def uptime_text(seconds):
    """開機時間：一天以上顯示「3 天 4 小時」。"""
    if seconds is None:
        return PLACEHOLDER
    minutes = int(seconds // 60)
    if minutes < MINUTES_PER_DAY:
        return duration_text(minutes)
    return f"{minutes // MINUTES_PER_DAY} 天 {minutes % MINUTES_PER_DAY // 60} 小時"


def on_ups_battery(power):
    return power.ups is not None and power.ups.on_battery


def header_text(power):
    """（大字瓦數, 說明, 語氣）；改由不斷電系統電池供電時語氣為警告，電量低時為嚴重。"""
    if power is None:
        return PLACEHOLDER, "正在讀取耗電…", TONE_NORMAL
    if power.source == SOURCE_NONE:
        return PLACEHOLDER, "無法取得整機耗電", TONE_NORMAL
    big = watts_short(power.system_w)
    if power.source == SOURCE_COMPONENTS:
        return big, "無法取得整機耗電 · 顯示已知元件合計", TONE_NORMAL
    if on_ups_battery(power):
        left = power.ups.time_left_min
        sub = "改由不斷電系統電池供電" + (
            "" if left is None else f" · 剩餘約 {left:.0f} 分鐘")
        return big, sub, TONE_CRITICAL if power.ups.low_battery else TONE_WARNING
    if power.average_w is None:
        return big, UPS_NOTE, TONE_NORMAL
    return big, f"3 分鐘平均 {watts_short(power.average_w)} · {UPS_NOTE}", TONE_NORMAL


def tray_label(power):
    """頂端列「耗電」指示器的文字，例如「288 W」。"""
    value = None if power is None else power.system_w
    return "— W" if value is None else watts_short(value)


def menu_line(power):
    """頂端列選單第一行。"""
    if power is None:
        return "耗電：讀取中"
    if power.system_w is None:
        return "耗電：無法取得"
    text = "耗電：" + watts_short(power.system_w)
    if power.source == SOURCE_COMPONENTS:
        return text + "（已知元件合計）"
    if power.average_w is None:
        return text
    return text + f"（3 分鐘平均 {watts_short(power.average_w)}）"


def breakdown_rows(breakdown):
    """（id, 名稱, 瓦數, 占整機比例）；只有整機讀值來自不斷電系統時才顯示比例。"""
    system = breakdown.system_w if breakdown.source == SOURCE_UPS else None
    rows = []
    for item in breakdown.items:
        share = ""
        if system and item.watts is not None:
            share = f"{item.watts / system * 100:.0f}%"
        rows.append((item.id, item.label, watts_text(item.watts), share))
    return tuple(rows)


def flow_model(power) -> Optional[FlowModel]:
    """沒有任何耗電數字時回傳 None（流向圖隱藏）。"""
    if power is None or power.system_w is None:
        return None
    total = "整機" if power.source == SOURCE_UPS else "已知元件合計"
    items = tuple((item.id, item.label, item.watts) for item in power.breakdown.items)
    return FlowModel(supply_label(power.supply), total, power.system_w, items)


def number_text(value, suffix):
    return PLACEHOLDER if value is None else f"{value:.0f}{suffix}"


def detail_rows(power):
    """詳細資訊的八個欄位（兩欄排列）。"""
    ups = None if power is None else power.ups
    available = ups is not None and not ups.comm_lost
    return (
        ("處理器", (power.processor if power else "") or PLACEHOLDER),
        ("機型", (power.model if power else "") or PLACEHOLDER),
        ("開機時間", uptime_text(power.uptime_s if power else None)),
        ("不斷電系統型號", (ups.model if ups else "") or PLACEHOLDER),
        ("市電電壓", number_text(ups.line_v if available else None, " V")),
        ("負載", percent(ups.load_pct) if available else PLACEHOLDER),
        ("額定功率", number_text(ups.nominal_w if available else None, " W")),
        ("預估續航", duration_text(ups.time_left_min if available else None)),
    )
