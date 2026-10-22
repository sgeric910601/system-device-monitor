"""更新頻率與省電模式：由設定與目前的供電狀態算出各取樣層的間隔（不載入 GTK）。

- 一般層（CPU、記憶體、網速、磁碟讀寫）＝更新頻率。
- 慢速層（不斷電系統、溫度、電池）＝ max(5, 更新頻率)。
- nvidia-smi 串流＝ max(2000, 更新頻率 × 1000) 毫秒。
- 詳細取樣與設備重新確認維持各自的頻率，但不比更新頻率快。
- 省電模式生效時更新頻率至少 2 秒，電力流向也不播放動畫（參考專案 sampleInterval）。
  「只在使用電池時」：筆電看變壓器是否接著，桌機看不斷電系統是否改用電池。
"""
from dataclasses import dataclass

from sdm.constants import Collect, Monitor, Schedule


@dataclass(frozen=True)
class Cadence:
    system: int         # 一般層（秒）
    slow: int           # 慢速層（秒）
    devices: int        # 設備重新確認（秒）
    detail: int         # 頁面顯示時的詳細取樣（秒）
    nvidia_ms: int      # nvidia-smi 串流（毫秒）
    saving: bool        # 省電模式是否生效


def effective_interval(interval, saving):
    """不在可選範圍內的值改用預設的 2 秒；省電模式生效時至少 2 秒。"""
    if interval not in Schedule.INTERVALS:
        interval = Schedule.DEFAULT_INTERVAL
    return max(Schedule.SAVING_MIN_INTERVAL, interval) if saving else interval


def cadence(interval, saving):
    base = effective_interval(interval, saving)
    return Cadence(system=base, slow=max(Collect.SLOW_INTERVAL, base),
                   devices=max(Collect.DEVICES_INTERVAL, base),
                   detail=max(Monitor.DETAIL_INTERVAL, base),
                   nvidia_ms=max(Schedule.NVIDIA_MIN_MS, base * Schedule.MILLISECONDS),
                   saving=saving)


def saving_active(power_saving, battery_only, on_external_power):
    """參考專案 powerSavingActive：開關打開，且（沒有限定電池，或目前沒有外部電源）。"""
    return power_saving and not (battery_only and on_external_power)


def external_power(power):
    """筆電：變壓器接著；桌機：不斷電系統沒有改用電池。還沒有快照時視為接著電源。"""
    if power is None:
        return True
    battery = getattr(power, "battery", None)
    if battery is not None:
        return bool(battery.ac_online)
    ups = getattr(power, "ups", None)
    return not (ups is not None and ups.on_battery)


def cadence_for(preferences, power):
    """由設定與目前的耗電快照算出各取樣層的間隔。"""
    saving = saving_active(preferences.power_saving, preferences.power_saving_on_battery_only,
                           external_power(power))
    return cadence(preferences.refresh_interval, saving)
