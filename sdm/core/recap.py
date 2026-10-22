"""今日電量回顧（純邏輯），移植參考專案 DailyBatteryRecap：

- 只看有電量的紀錄（桌機的紀錄沒有電量）；午夜前最後一筆當作今天開始的狀態。
- 依接電／用電池分段，每段只算頭尾的淨變化（接電時在上限附近 ±1% 跳動不重複計算），
  插拔之間的變化算進前一段。
- 兩筆紀錄中間斷了很久（睡眠、看板沒在執行）最多算 30 分鐘。
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional

from sdm.constants import Battery
from sdm.core.battery import round_half_up

SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class DailyRecap:
    used_percent: int = 0
    charged_percent: int = 0
    plug_in_count: int = 0
    unplugged_minutes: int = 0
    longest_unplugged_minutes: int = 0
    start_percent: Optional[int] = None     # 今天開始（或第一筆紀錄）的電量；沒有紀錄時為 None
    end_percent: Optional[int] = None

    @property
    def net_change(self):
        if self.start_percent is None or self.end_percent is None:
            return None
        return self.end_percent - self.start_percent


def day_bounds(day, tz=None):
    """day 所在那一天的（午夜、隔天午夜）Unix 時間；tz 為 None 時用本機時區（含日光節約）。"""
    start = datetime.fromtimestamp(day, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def segment_change(plugged, start_pct, end_pct, used, charged):
    """結束一段：接電段只算補回，用電池段只算用掉。"""
    net = end_pct - start_pct
    if plugged:
        return used, charged + max(0, net)
    return used + max(0, -net), charged


def walk(points):
    used = charged = plugs = 0
    unplugged = stretch = longest = 0.0
    plugged, start_pct = points[0].plugged, points[0].pct
    for left, right in zip(points, points[1:]):
        elapsed = min(max(0.0, right.t - left.t), Battery.RECAP_GAP_SECONDS)
        if not left.plugged:
            unplugged += elapsed
            stretch += elapsed
            longest = max(longest, stretch)
        if left.plugged == right.plugged:
            continue
        used, charged = segment_change(plugged, start_pct, right.pct, used, charged)
        plugs += 1 if right.plugged else 0
        stretch = 0.0
        plugged, start_pct = right.plugged, right.pct
    used, charged = segment_change(plugged, start_pct, points[-1].pct, used, charged)
    return dict(used_percent=used, charged_percent=charged, plug_in_count=plugs,
                unplugged_minutes=round_half_up(unplugged / SECONDS_PER_MINUTE),
                longest_unplugged_minutes=round_half_up(longest / SECONDS_PER_MINUTE))


def make_recap(samples, day, tz=None):
    """依歷史紀錄算出 day 那一天的回顧；沒有當天的電量紀錄時回傳空的 DailyRecap。"""
    start, end = day_bounds(day, tz)
    ordered = sorted((item for item in samples if item.pct is not None), key=lambda item: item.t)
    today = [item for item in ordered if start <= item.t < end]
    if not today:
        return DailyRecap()
    before = [item for item in ordered if item.t < start]
    points = ([replace(before[-1], t=start)] if before else []) + today
    return DailyRecap(start_percent=points[0].pct, end_percent=today[-1].pct, **walk(points))
