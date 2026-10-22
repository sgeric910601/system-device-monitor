"""系統監控頁的 2 分鐘曲線（參考專案 MonitorHistory）。

最多 120 點，而且只保留最新一點之前 120 秒內的點；更新間隔 2 秒時約 61 點。
不可變：appended() 回傳新的物件。值為 None 代表那一點沒有資料，曲線在那裡斷開。
"""
from dataclasses import dataclass
from typing import Optional

from sdm.constants import Monitor


@dataclass(frozen=True)
class MonitorPoint:
    t: float                    # 單調時鐘秒數
    a: Optional[float]          # 第一條線（CPU、已使用、讀取、下載）
    b: Optional[float] = 0.0    # 第二條線（GPU、寫入、上傳）


@dataclass(frozen=True)
class MonitorHistory:
    points: tuple = ()

    def appended(self, a, b=0.0, t=0.0):
        points = (self.points + (MonitorPoint(t, a, b),))[-Monitor.HISTORY_CAPACITY:]
        cutoff = t - Monitor.HISTORY_WINDOW_SECONDS
        keep = next((index for index, point in enumerate(points) if point.t >= cutoff),
                    len(points))
        return MonitorHistory(points[keep:])

    def latest(self):
        return self.points[-1] if self.points else None

    def max_value(self):
        """兩條線的最大值（Y 軸上限用）；沒有資料時為 0。"""
        values = [value for point in self.points for value in (point.a, point.b)
                  if value is not None]
        return max(values, default=0.0)
