"""耗電與電量歷史的純邏輯：列驗證、檔案格式、保留期限、圖表點與拔電時間。

檔案格式：{"format": 1, "samples": [[t, pct, watts, plugged, charging, ups_pct, ups_online], ...]}
t 為 Unix 時間（秒）；桌機沒有電池，pct 為 None。
圖表降採樣與分段規則與參考專案 HistoryStore.points 相同。
"""
from dataclasses import dataclass
import json
import math
from typing import Optional

from sdm.constants import History, Power

ROW_LENGTH = 7


class Undecodable(ValueError):
    """檔案完全無法解讀（不是 JSON、結構不對或格式版本無效）。"""


class NewerFormat(ValueError):
    """檔案來自較新版本，不能用目前的格式覆寫。"""


@dataclass(frozen=True)
class HistorySample:
    t: float
    pct: Optional[int]
    watts: Optional[float]
    plugged: bool
    charging: bool
    ups_pct: Optional[float]
    ups_online: Optional[bool]

    def to_row(self):
        return [self.t, self.pct, self.watts, self.plugged, self.charging, self.ups_pct,
                self.ups_online]


@dataclass(frozen=True)
class ChartPoint:
    """圖表上的一點；segment 不同的點不連線，watts_segment 是耗電曲線的分段。"""
    id: int
    t: float
    pct: Optional[float]
    watts: Optional[float]
    ups_pct: Optional[float]
    plugged: bool
    segment: int
    watts_segment: int


def is_real(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def optional_real(value, low, high, high_inclusive=True):
    if value is None:
        return True
    return is_real(value) and low <= value and (value <= high if high_inclusive else value < high)


def valid_sample(sample, now):
    """每個欄位的型別與範圍都正確，且時間不超過 now 之後 5 分鐘。"""
    pct_ok = sample.pct is None or (type(sample.pct) is int and 0 <= sample.pct <= 100)
    return (is_real(sample.t) and 0 < sample.t <= now + History.FUTURE_TOLERANCE_SECONDS
            and pct_ok and optional_real(sample.watts, 0, Power.SYSTEM_MAX_W, False)
            and type(sample.plugged) is bool and type(sample.charging) is bool
            and optional_real(sample.ups_pct, 0, 100)
            and (sample.ups_online is None or type(sample.ups_online) is bool))


def sample_from_row(row, now):
    """把一列轉成 HistorySample；任何欄位無效時回傳 None。"""
    if not isinstance(row, list) or len(row) != ROW_LENGTH:
        return None
    candidate = HistorySample(*row)
    return candidate if valid_sample(candidate, now) else None


def decode_history(data, now):
    """回傳（依時間排序的有效樣本, 捨棄的列數）；無法解讀時拋出 Undecodable。"""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise Undecodable("not JSON") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("samples"), list):
        raise Undecodable("unexpected structure")
    version = raw.get("format")
    if type(version) is int and version > History.FORMAT:
        raise NewerFormat(str(version))
    if type(version) is not int or version != History.FORMAT:
        raise Undecodable("unknown format")
    samples = [sample_from_row(row, now) for row in raw["samples"]]
    valid = [item for item in samples if item is not None]
    return tuple(sorted(valid, key=lambda item: item.t)), len(samples) - len(valid)


def encode_history(samples):
    """精簡 JSON（不縮排），一週約一萬筆時檔案較小、寫入較快。"""
    data = {"format": History.FORMAT, "samples": [item.to_row() for item in samples]}
    return (json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def trim(samples, now, retention=History.RETENTION_SECONDS):
    """只保留最近 retention 秒內的樣本。"""
    cutoff = now - retention
    return tuple(item for item in samples if item.t >= cutoff)


def mean(values):
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def bucket_point(chunk):
    """一個區間的平均值：時間取第一筆，接電狀態只要有一筆接電就算接電。"""
    return dict(t=chunk[0].t, pct=mean(item.pct for item in chunk),
                watts=mean(item.watts for item in chunk),
                ups_pct=mean(item.ups_pct for item in chunk),
                plugged=any(item.plugged for item in chunk))


def chart_points(samples, range_s, now, max_points=History.MAX_POINTS):
    """取 range_s 秒內的樣本並降採樣到 max_points 以內（參考專案 points()）。"""
    selected = [item for item in samples if item.t >= now - range_s]
    if not selected:
        return ()
    size = max(1, math.ceil(len(selected) / max_points))
    out, segment, watts_segment, previous = [], 0, 0, None
    for start in range(0, len(selected), size):
        values = bucket_point(selected[start:start + size])
        if previous is not None:
            elapsed = values["t"] - previous.t
            if values["watts"] is None or previous.watts is None or \
                    elapsed > History.WATTS_GAP_SECONDS:
                watts_segment += 1
            if elapsed > History.GAP_SECONDS:
                segment += 1
            elif values["plugged"] != previous.plugged:
                # 換顏色時把上一個點複製到新段，線才會接起來
                segment += 1
                out.append(ChartPoint(len(out), previous.t, previous.pct, None,
                                      previous.ups_pct, values["plugged"], segment,
                                      watts_segment))
        previous = ChartPoint(len(out), segment=segment, watts_segment=watts_segment, **values)
        out.append(previous)
    return tuple(out)


def clamp_to_boot(unplugged, boot_time):
    """拔電時間早於這次開機（中間關過機）時改從開機算起；睡眠照算。"""
    if boot_time is None or boot_time <= unplugged:
        return unplugged
    return boot_time


def last_unplugged(samples, now, boot_time):
    """最近一次從接電變成用電池的時間；仍接電或紀錄太舊時回傳 None。"""
    if not samples:
        return None
    last = samples[-1]
    if last.plugged or now - last.t >= History.UNPLUGGED_MAX_AGE_SECONDS:
        return None
    plugged = [index for index, item in enumerate(samples) if item.plugged]
    if not plugged:
        return None
    return clamp_to_boot(samples[plugged[-1] + 1].t, boot_time)


def parse_boot_time(stat_text):
    """從 /proc/stat 的 btime 列取得開機時間（Unix 秒）。"""
    for line in stat_text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "btime":
            try:
                return float(int(fields[1]))
            except ValueError:
                return None
    return None
