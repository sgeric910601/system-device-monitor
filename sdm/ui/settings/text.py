"""設定視窗的文字（不載入 GTK）：選項名稱、說明與說明文件的位置。

用詞沿用參考專案 SettingsView；有電池的電腦稱「電源」，桌機稱「耗電」。
"""
from pathlib import Path

from sdm.constants import History, Schedule
from sdm.core.preference_specs import PREFERENCE_SPECS
from sdm.core.schedule import saving_active

INTERVALS = tuple((seconds, "每秒更新" if seconds == 1 else f"每 {seconds} 秒更新")
                  for seconds in Schedule.INTERVALS)
DISPLAY_FORMATS = (("icon_percent", "圖示＋百分比"), ("icon_only", "只有圖示"),
                   ("percent_only", "只有百分比"), ("icon_time", "圖示＋剩餘時間"),
                   ("icon_percent_time", "圖示內百分比＋剩餘時間"))
TRAY_MODES = (("split", "分開顯示（每個項目一個圖示）"), ("merged", "合併成一個"))
SAVING_BASE = "省電模式：電力流向改用靜態圖、不播放動畫，更新頻率至少每 2 秒。"
PACKAGE_DOCS = Path("/usr/share/doc/system-device-monitor")
DOCS = (("專案說明", "README.md"), ("使用說明", "docs/USAGE.md"), ("隱私說明", "docs/PRIVACY.md"),
        ("安全說明", "docs/SECURITY.md"))
SECONDS_PER_MINUTE = 60
UPS_OPTION_STEP = 5         # 不斷電系統低電量門檻的下拉選單每 5% 一格（設定檔可存任何整數）


def percent_options(low, high, step, extra=()):
    """（值、「20%」）：low–high 每 step 一格，另外加上 extra（例如設定檔中的自訂值）。"""
    values = sorted(set(range(low, high + 1, step)) | set(extra))
    return tuple((value, f"{value}%") for value in values)


def spec_options(key):
    spec = PREFERENCE_SPECS[key]
    return percent_options(spec.low, spec.high, spec.step)


LOW_THRESHOLDS = spec_options("low_threshold")
FULL_THRESHOLDS = spec_options("full_threshold")


def ups_thresholds(current):
    """不斷電系統低電量門檻：每 5% 一格，並保留目前的值（可能是設定檔中的其他整數）。"""
    spec = PREFERENCE_SPECS["ups_low_threshold"]
    return percent_options(spec.low, spec.high, UPS_OPTION_STEP, (current,))


def power_word(laptop):
    return "電源" if laptop else "耗電"


def scope_choices(laptop):
    word = power_word(laptop)
    return ((False, f"只看{word}"), (True, f"{word}＋系統監控"))


def every(seconds):
    return "每秒" if seconds == 1 else f"每 {seconds} 秒"


def power_saving_note(preferences, external_power):
    """參考專案 powerSavingNote。"""
    if preferences.power_saving and preferences.power_saving_on_battery_only and external_power:
        return SAVING_BASE + "目前接著電源，已暫時恢復一般模式。"
    active = saving_active(preferences.power_saving, preferences.power_saving_on_battery_only,
                           external_power)
    if active and preferences.refresh_interval < Schedule.SAVING_MIN_INTERVAL:
        return SAVING_BASE + f"（目前{every(Schedule.SAVING_MIN_INTERVAL)}更新）"
    return SAVING_BASE + "更新越慢越省電。"


def cadence_note(cadence):
    minutes = History.RECORD_INTERVAL_SECONDS // SECONDS_PER_MINUTE
    history = "每分鐘" if minutes == 1 else f"每 {minutes} 分鐘"
    return (f"CPU／記憶體／網速：{every(cadence.system)}・"
            f"溫度／不斷電系統／電池：{every(cadence.slow)}・耗電歷史：{history}記錄一次")


def monitors_note(preferences, laptop):
    if not preferences.system_monitors:
        return f"關閉時頂端列只有{power_word(laptop)}項目，四個模組都不會取樣，最省電。"
    return "開啟的模組有自己的頁面與頂端列項目；關閉的模組立即停止取樣並隱藏。"


def doc_links(root, package=PACKAGE_DOCS):
    """（名稱、路徑）：原始碼資料夾優先，其次是套件安裝的位置；都找不到的不列出。"""
    links = []
    for title, name in DOCS:
        for base in (Path(root), Path(package)):
            if (base/name).is_file():
                links.append((title, base/name))
                break
    return tuple(links)
