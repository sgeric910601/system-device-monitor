"""耗電與電量歷史的純邏輯：列驗證、檔案格式、保留期限、圖表點與拔電時間。

檔案格式：{"format": 1, "samples": [[t, pct, watts, plugged, charging, ups_pct, ups_online], ...]}
t 為 Unix 時間（秒）；桌機沒有電池，pct 為 None。
由 UPower 補回的紀錄另外放在選用的 "imported" 欄位（見 sdm.core.backfill），格式版本不變：
舊版只讀 "samples"，照常讀得懂這個檔案（存檔時會把補回的部分丟掉，之後再補一次）。
圖表的分段與複製點規則沿用參考專案 HistoryStore.points，分區間方式刻意不同（見 chart_points）。
"""
from dataclasses import dataclass
import heapq
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
    imported: bool = False      # 由 UPower 的紀錄補回（不寫進列，放在檔案的 "imported" 欄位）

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
    imported: bool = False      # 補回的電量（畫成虛線）


def is_real(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def optional_real(value, low, high, high_inclusive=True):
    if value is None:
        return True
    return is_real(value) and low <= value and (value <= high if high_inclusive else value < high)


def valid_sample(sample, now=None):
    """每個欄位的型別與範圍都正確；有 now 時（新紀錄）時間也不能超過 now 之後 5 分鐘。

    檔案中已存的列不檢查時間（now 為 None），時鐘暫時落後時不會把資料當成無效丟掉。
    """
    if not (is_real(sample.t) and sample.t > 0):
        return False
    if now is not None and sample.t > now + History.FUTURE_TOLERANCE_SECONDS:
        return False
    pct_ok = sample.pct is None or (type(sample.pct) is int and 0 <= sample.pct <= 100)
    return (pct_ok and optional_real(sample.watts, 0, Power.SYSTEM_MAX_W, False)
            and type(sample.plugged) is bool and type(sample.charging) is bool
            and optional_real(sample.ups_pct, 0, 100)
            and (sample.ups_online is None or type(sample.ups_online) is bool)
            and type(sample.imported) is bool)


def sample_from_row(row, now=None):
    """把一列轉成 HistorySample；任何欄位無效時回傳 None。"""
    if not isinstance(row, list) or len(row) != ROW_LENGTH:
        return None
    candidate = HistorySample(*row)
    return candidate if valid_sample(candidate, now) else None


def load_document(data):
    """解析檔案並檢查結構與格式版本，回傳 JSON 物件；無法解讀時拋出 Undecodable。"""
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
    return raw


def decode_rows(rows):
    """回傳（依時間排序的有效樣本、捨棄的列數）。"""
    samples = [sample_from_row(row) for row in rows]
    valid = [item for item in samples if item is not None]
    return tuple(sorted(valid, key=lambda item: item.t)), len(samples) - len(valid)


def decode_history(data):
    """回傳（依時間排序的有效樣本、捨棄的列數）；無法解讀時拋出 Undecodable。"""
    return decode_rows(load_document(data)["samples"])


def encode_history(samples, imported=None):
    """精簡 JSON（不縮排），一週約一萬筆時檔案較小、寫入較快。

    imported 是補回紀錄的 JSON 欄位（sdm.core.backfill.imported_section）；None 時不寫這個欄位，
    沒有補回時檔案與先前的版本完全相同。
    """
    data = {"format": History.FORMAT, "samples": [item.to_row() for item in samples]}
    if imported is not None:
        data["imported"] = imported
    return (json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def trim(samples, now, retention=History.RETENTION_SECONDS):
    """只保留最近 retention 秒內的樣本；now 早於最新一筆（時鐘落後）時完全不修剪。"""
    if samples and now < samples[-1].t:
        return tuple(samples)
    cutoff = now - retention
    return tuple(item for item in samples if item.t >= cutoff)


def mean(values):
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def bucket_point(chunk):
    """一個區間的平均值：時間取第一筆，接電狀態只要有一筆接電就算接電。

    同一區間同時有自己的與補回的紀錄時（點數太多、合併最舊的段才會發生）只用自己的紀錄，
    兩種來源的數值絕不平均在一起。
    """
    own = [item for item in chunk if not item.imported]
    chunk = own or chunk
    return dict(t=chunk[0].t, pct=mean(item.pct for item in chunk),
                watts=mean(item.watts for item in chunk),
                ups_pct=mean(item.ups_pct for item in chunk),
                plugged=any(item.plugged for item in chunk), imported=not own)


def split_runs(selected):
    """在超過 600 秒的空白處與來源（自己的、補回的）改變處切開，不會被平均成同一點。"""
    runs, current = [], [selected[0]]
    for before, item in zip(selected, selected[1:]):
        if item.t - before.t > History.WATTS_GAP_SECONDS or item.imported != before.imported:
            runs.append(current)
            current = []
        current.append(item)
    runs.append(current)
    return runs


def merge_oldest(groups, limit):
    """段數超過 limit 時，從最舊的開始兩兩合併：只犧牲最舊資料的細節。"""
    while len(groups) > limit:
        pairs = min(len(groups) - limit, len(groups) // 2)
        merged = [groups[2 * index] + groups[2 * index + 1] for index in range(pairs)]
        groups = merged + groups[2 * pairs:]
    return groups


def chunk_size(groups, budget):
    """最小的每區間筆數，讓各段依此切分後的區間總數不超過 budget。"""
    low, high = 1, max(len(group) for group in groups)
    while low < high:
        middle = (low + high) // 2
        if sum(math.ceil(len(group) / middle) for group in groups) <= budget:
            high = middle
        else:
            low = middle + 1
    return low


def plan_buckets(selected, budget):
    """區間數不超過 budget；沒有空白時每區間 ceil(n / budget) 筆，與參考專案的切法一樣。"""
    groups = merge_oldest(split_runs(selected), budget)
    size = chunk_size(groups, budget)
    return [group[start:start + size] for group in groups
            for start in range(0, len(group), size)]


def bucket_points(buckets):
    """依參考專案規則產生圖表點；間隔量的是上一區間最後一筆到這一區間第一筆。"""
    out, segment, watts_segment, previous, last_t = [], 0, 0, None, None
    for chunk in buckets:
        values = bucket_point(chunk)
        if previous is not None:
            elapsed = values["t"] - last_t
            if values["watts"] is None or previous.watts is None or \
                    elapsed > History.WATTS_GAP_SECONDS:
                watts_segment += 1
            if elapsed > History.GAP_SECONDS:
                segment += 1
            else:
                segment = join(out, previous, values, segment, watts_segment)
        previous = ChartPoint(len(out), segment=segment, watts_segment=watts_segment, **values)
        out.append(previous)
        last_t = chunk[-1].t
    return tuple(out)


def join(out, previous, values, segment, watts_segment):
    """換來源或換顏色時開新的一段，並加一個複製點讓線接起來；回傳這一點的段號。

    自己的 → 補回的：上一點複製到補回段；補回的 → 自己的：這一點先複製到補回段，
    虛線一直畫到自己的紀錄為止。換顏色時把上一個點複製到新段（參考專案的作法）。
    """
    if values["imported"] != previous.imported:
        if previous.imported:
            out.append(ChartPoint(len(out), values["t"], values["pct"], None, values["ups_pct"],
                                  previous.plugged, segment, watts_segment, imported=True))
        else:
            out.append(ChartPoint(len(out), previous.t, previous.pct, None, previous.ups_pct,
                                  values["plugged"], segment + 1, watts_segment, imported=True))
        return segment + 1
    if values["plugged"] != previous.plugged:
        out.append(ChartPoint(len(out), previous.t, previous.pct, None, previous.ups_pct,
                              values["plugged"], segment + 1, watts_segment,
                              imported=values["imported"]))
        return segment + 1
    return segment


def chart_points(samples, range_s, now, max_points=History.MAX_POINTS, imported=()):
    """取 range_s 秒內的樣本並降採樣，點數（含複製點）保證不超過 max_points。

    imported 是由 UPower 補回的樣本（依時間排序）：與自己的紀錄依時間合併，但分段、
    分區間都依來源切開，補回的點 imported 為 True（圖表畫成虛線）。

    分段、斷線與複製點的規則沿用參考專案 HistoryStore.points()，但分區間刻意不同：
    區間不跨越超過 600 秒的空白（參考的作法在 7 天範圍會讓耗電線畫不出來，
    空白也會被平均掉），空白太多時從最舊的資料開始合併；複製點也計入點數，
    超過時縮小區間預算重算。只有在每區間一筆、且樣本加複製點不超過 max_points 時，
    結果才與參考相同。
    """
    start = now - range_s
    selected = list(heapq.merge((item for item in samples if item.t >= start),
                                (item for item in imported if item.t >= start),
                                key=lambda item: item.t))
    if not selected:
        return ()
    limit = max(1, max_points)
    budget = limit
    while True:
        points = bucket_points(plan_buckets(selected, budget))
        if len(points) <= limit or budget == 1:
            return points
        budget = max(1, min(budget - 1, budget * limit // len(points)))


def clamp_to_boot(unplugged, boot_time):
    """拔電時間早於這次開機（中間關過機）時改從開機算起；睡眠照算。"""
    if boot_time is None or boot_time <= unplugged:
        return unplugged
    return boot_time


def still_unplugged(samples, now):
    """最後一筆是用電池，而且不超過 10 分鐘前。"""
    return bool(samples) and not samples[-1].plugged and \
        now - samples[-1].t < History.UNPLUGGED_MAX_AGE_SECONDS


def unplugged_since(samples, boot_time):
    """最後一次接電之後第一筆用電池的時間（不看紀錄新舊）；從沒接電或仍接電時回傳 None。"""
    plugged = [index for index, item in enumerate(samples) if item.plugged]
    if not plugged or plugged[-1] + 1 >= len(samples):
        return None
    return clamp_to_boot(samples[plugged[-1] + 1].t, boot_time)


def last_unplugged(samples, now, boot_time):
    """最近一次從接電變成用電池的時間；仍接電或紀錄太舊時回傳 None。"""
    if not still_unplugged(samples, now):
        return None
    return unplugged_since(samples, boot_time)


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
