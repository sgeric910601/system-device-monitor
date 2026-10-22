"""頂端列的文字與內容（不載入 GTK）：分開顯示的各項目、合併成一個的標籤，以及選單的每一行。

耗電項目的文字依「顯示格式」（參考專案 updateStatusItem）：
- 圖示＋百分比「82%」、只有圖示（不顯示文字）、只有百分比（充電中加 ⚡）。
- 圖示＋剩餘時間「3:25」：沒有時間時退回百分比；
  圖示內百分比＋剩餘時間：沒有時間時顯示「—」，已充飽／接電／已達上限直接寫出狀態。
- 桌機一律顯示整機瓦數，只有圖示時不顯示文字。
合併模式把開啟的項目依固定順序（耗電、CPU、溫度、記憶體、GPU、網路）以「｜」串成一個標籤；
筆電的時間放在括號裡，例如「82%（3:25）｜CPU 12%」。
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional

from sdm.core.battery import CHARGING, DISCHARGING, FULL, LIMIT_REACHED
from sdm.core.fmt import PLACEHOLDER, percent, size, temperature, value_text
from sdm.core.settings import LEGACY_KEYS
from sdm.ui import battery_text, power_text
from sdm.ui.tray_icons import power_glyph

SPLIT, MERGED = "split", "merged"
POWER = "power"
ITEMS = (POWER,) + LEGACY_KEYS             # 固定順序
SEPARATOR = "｜"
MODULES = {"cpu": "monitor_cpu", "temperature": "monitor_cpu", "gpu": "monitor_cpu",
           "ram": "monitor_memory", "network": "monitor_network"}
PAGES = {POWER: "power", "cpu": "cpu", "temperature": "cpu", "gpu": "cpu", "ram": "memory",
         "network": "network", "ups": "power"}
TIME_MODES = ("icon_time", "icon_percent_time")
INLINE = "icon_percent_time"
STATE_WORDS = {FULL: "已充飽", LIMIT_REACHED: "已達上限"}
PLUGGED_WORD = "接電"
MINUTES_PER_HOUR = 60
KIB = 1024
COMPACT_DECIMAL_BELOW = 10      # 10 MB/s 以下保留一位小數


@dataclass(frozen=True)
class TrayView:
    mode: str
    labels: Mapping             # 項目 → 分開顯示時的文字
    visible: tuple              # 開啟的項目（固定順序）
    merged_label: str
    glyph: Optional[object]     # 耗電圖示（tray_icons 的 BatteryGlyph／DesktopGlyph），None 為不顯示
    menu: tuple                 # （項目、文字、要開啟的頁面）


def clock(minutes):
    """參考專案 Format.clock：「3:25」；沒有時間時回傳 None。"""
    if minutes is None or minutes < 0:
        return None
    minutes = int(minutes)
    return f"{minutes // MINUTES_PER_HOUR}:{minutes % MINUTES_PER_HOUR:02d}"


def is_laptop(power):
    return getattr(power, "battery", None) is not None


def laptop_time(power, inline):
    """時間模式的文字：剩餘或充滿時間；沒有時間時依狀態給文字，或退回 None（由呼叫端決定）。"""
    e, state = power.estimates, power.state
    if state == DISCHARGING or e.draining_on_ac:
        return clock(e.headline_to_empty)
    if state == CHARGING:
        return clock(e.headline_to_full)
    if not inline:
        return None
    return STATE_WORDS.get(state, PLUGGED_WORD)


def laptop_label(power, display):
    percent_text = battery_text.tray_label(power)
    if display == "icon_only":
        return ""
    if display == "percent_only":
        return ("⚡" if power.state == CHARGING else "") + percent_text
    if display in TIME_MODES:
        inline = display == INLINE
        text = laptop_time(power, inline)
        return text if text is not None else (PLACEHOLDER if inline else percent_text)
    return percent_text


def power_label(power, display):
    """耗電項目分開顯示時的文字。"""
    if is_laptop(power):
        return laptop_label(power, display)
    return "" if display == "icon_only" else power_text.tray_label(power)


def merged_power(power, display):
    """合併標籤中的耗電部分：筆電在時間模式把時間放在括號裡。"""
    if display == "icon_only":
        return ""
    if not is_laptop(power) or display not in TIME_MODES:
        return power_label(power, display)
    detail = laptop_time(power, True)
    percent_text = battery_text.tray_label(power)
    return percent_text if detail is None else f"{percent_text}（{detail}）"


def compact_rate(value):
    """合併標籤用的短速率：「340K」「1.2M」（1024 進位，與分開顯示相同）。"""
    if value is None:
        return PLACEHOLDER
    kib = max(0.0, float(value)) / KIB
    if kib < KIB:
        return f"{kib:.0f}K"
    mib = kib / KIB
    return f"{mib:.1f}M" if mib < COMPACT_DECIMAL_BELOW else f"{mib:.0f}M"


def gpu_usage(fast, slow):
    gpu = fast.get("gpu") or slow.get("gpu")
    return gpu.usage if gpu else None


def item_texts(fast, slow, compact=False):
    """CPU、溫度、記憶體、GPU、網路在頂端列的文字。"""
    rate = compact_rate if compact else (lambda value: size(value, True))
    gap = " "
    return {"cpu": "CPU " + percent(fast.get("cpu")),
            "temperature": temperature(slow.get("cpu_temp")),
            "ram": "RAM " + percent(fast.get("ram")),
            "gpu": "GPU " + percent(gpu_usage(fast, slow)),
            "network": "↓" + rate(fast.get("rx")) + gap + "↑" + rate(fast.get("tx"))}


def module_enabled(preferences, key):
    module = MODULES.get(key)
    return module is None or (preferences.system_monitors and preferences[module])


def enabled_items(legacy, preferences):
    """頂端列要顯示的項目：耗電看 tray_power，其他看舊版勾選與監控模組。"""
    shown = []
    for key in ITEMS:
        on = preferences.tray_power if key == POWER else legacy.get(key, False)
        if on and module_enabled(preferences, key):
            shown.append(key)
    return tuple(shown)


def merged_label(parts):
    return SEPARATOR.join(part for part in parts if part)


def power_menu_line(power):
    if is_laptop(power):
        return battery_text.menu_line(power)
    return power_text.menu_line(power)


def ups_line(ups):
    return ("UPS " + percent(ups.charge_pct) + " · 續航 " +
            value_text(ups.time_left_min, " 分鐘"))


def menu_lines(power, fast, slow, preferences):
    """選單：每個項目一行（不論是否顯示在頂端列），關閉的監控模組不列出。"""
    texts = item_texts(fast, slow)
    titled = {"temperature": "CPU 溫度 " + texts["temperature"],
              "ram": "記憶體 " + percent(fast.get("ram")),
              "network": "網速 " + texts["network"]}
    lines = [(POWER, power_menu_line(power), PAGES[POWER])]
    for key in LEGACY_KEYS:
        if module_enabled(preferences, key):
            lines.append((key, titled.get(key, texts[key]), PAGES[key]))
    ups = slow.get("ups") or getattr(power, "ups", None)
    if ups is not None:
        lines.append(("ups", ups_line(ups), PAGES["ups"]))
    return tuple(lines)


def tray_view(power, fast, slow, legacy, preferences):
    display = preferences.tray_display
    visible = enabled_items(legacy, preferences)
    labels = dict(item_texts(fast, slow), power=power_label(power, display))
    compact = dict(item_texts(fast, slow, compact=True), power=merged_power(power, display))
    return TrayView(
        mode=preferences.tray_mode, labels=MappingProxyType(labels), visible=visible,
        merged_label=merged_label(tuple(compact[key] for key in visible)),
        glyph=power_glyph(power, display, preferences.low_threshold),
        menu=menu_lines(power, fast, slow, preferences))
