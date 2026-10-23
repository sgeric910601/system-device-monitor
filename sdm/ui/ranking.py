"""「電源」頁的耗電排行（不載入 GTK）：使用第 3 階段的各 App CPU 取樣（程序群組）。

- 有 CPU 功耗時：各 App 的估算瓦數＝CPU 功耗 × 該 App 佔所有 App CPU 時間的比例，
  註明「依 CPU 佔比估算」。
- 沒有 CPU 功耗時（沒有開啟管理員 CPU 功耗取樣，也沒有已確認校正的 APU PPT）：
  依 CPU 使用率排序並註明原因。
只在「電源」頁顯示時取樣（sdm.core.tiers）。
"""
from sdm.core.apps import rank
from sdm.ui.app_names import CPU, display_names, placeholder, top_rows
from sdm.ui.monitor_text import CALCULATING

CPU_NOTE = "目前無法取得 CPU 功耗，依 CPU 使用率排序"
ESTIMATE_NOTE = "依 CPU 佔比估算"


def estimated_rows(apps, names, cpu_watts):
    items = tuple((usage.app_id, usage.core_percent) for usage in apps.sample.usages)
    total = sum(value for _, value in items if value is not None and value > 0)
    ranked = rank(items, names.key_for(apps.labels))
    shown = display_names(ranked, names, apps.labels)
    return tuple((name, names.resolve(row.app_ids[0], apps.labels)[1],
                  f"{row.value / total * cpu_watts:.1f} W", f"CPU {row.value:.1f}%")
                 for row, name in zip(ranked, shown))


def power_ranking(apps, names, cpu_watts=None):
    """（排行列、說明、沒有資料時的文字）；排行列為（名稱、圖示、數值、次要文字）。"""
    note = CPU_NOTE if cpu_watts is None else ESTIMATE_NOTE
    if apps is None or not apps.sample.ready:
        return (), note, CALCULATING
    if cpu_watts is None:
        return top_rows(apps, names, CPU, apps.cores), note, placeholder(apps, CPU)
    return estimated_rows(apps, names, cpu_watts), note, placeholder(apps, CPU)
