"""由 UPower 自己的電量紀錄補回本程式沒有紀錄的時段（純邏輯，不連任何匯流排）。

- 系列：筆電電池電量（pct）與桌機不斷電系統電量（ups_pct），沿用歷史紀錄的欄位名稱；
  只補本程式目前有在記錄的系列（最新一筆自己的紀錄有這個值）。耗電不補：UPower 的 rate 是
  電池充放電的速率，不是本程式記錄的整機耗電。
- 空白：保留期限（7 天）內第一筆自己的紀錄之前，以及兩筆自己的紀錄相隔超過 15 分鐘之處；
  兩端各留 60 秒不補。自己的紀錄絕不覆寫、也不重排。
- 每段空白每個系列只處理一次：處理過（即使 UPower 在那段時間沒有紀錄）就記成 Span 存進檔案。
- UPower 的點逐一檢查型別、有限值、0–100、時間在保留期限內且不晚於現在 5 分鐘；
  丟掉「0 % 狀態不明」的邊界假點，依時間排序後同一時間只留一點，數量有上限。
- 檔案：補回的列與處理過的空白放在 history.json 的 "imported" 欄位，只存系列名稱與數值，
  不存任何裝置路徑、型號或序號。
"""
from dataclasses import dataclass, replace
import math

from sdm.constants import Backfill, History
from sdm.core.battery import round_half_up
from sdm.core.history import HistorySample, is_real, sample_from_row, trim, valid_sample

SERIES_BATTERY = "pct"          # 與 HistorySample 的欄位同名
SERIES_UPS = "ups_pct"
SERIES = (SERIES_BATTERY, SERIES_UPS)
PERCENT = 100
UPS_DECIMALS = 1                # 與自己的不斷電系統紀錄相同的小數位數
POINT_LENGTH = 3                # GetHistory 的每一點：（時間、數值、狀態）
STATE_UNKNOWN, STATE_CHARGING, STATE_DISCHARGING = 0, 1, 2
STATE_FULL, STATE_PENDING_CHARGE, STATE_MAX = 4, 5, 6
PLUGGED_STATES = frozenset({STATE_CHARGING, STATE_FULL, STATE_PENDING_CHARGE})
ON_BATTERY_STATES = frozenset({STATE_DISCHARGING})


class InvalidImported(ValueError):
    """檔案中的 "imported" 欄位無法使用（整個欄位捨棄，之後再補一次）。"""


@dataclass(frozen=True)
class UpowerPoint:
    t: int
    value: float
    state: int


@dataclass(frozen=True)
class Gap:
    """沒有自己紀錄的時段（start、end 為空白兩側自己紀錄的時間，或保留期限的起點）。"""
    start: float
    end: float


@dataclass(frozen=True)
class Span:
    """某個系列已經處理過的空白。"""
    series: str
    start: float
    end: float


@dataclass(frozen=True)
class Imported:
    samples: tuple = ()     # 補回的 HistorySample（imported 為 True、沒有耗電）
    spans: tuple = ()       # 處理過的空白（Span）


@dataclass(frozen=True)
class Request:
    series: str
    gaps: tuple


@dataclass(frozen=True)
class Plan:
    requests: tuple = ()
    timespan: int = 0       # GetHistory 往回讀的秒數

    @property
    def series(self):
        return tuple(request.series for request in self.requests)


# ── UPower 的點 ─────────────────────────────────────────


def valid_point(entry, now):
    if not isinstance(entry, (tuple, list)) or len(entry) != POINT_LENGTH:
        return None
    t, value, state = entry
    if type(t) is not int or type(state) is not int or not is_real(value):
        return None
    if not (0 <= value <= PERCENT and 0 <= state <= STATE_MAX):
        return None
    if t <= 0 or t < now - History.RETENTION_SECONDS or \
            t > now + History.FUTURE_TOLERANCE_SECONDS:
        return None
    if state == STATE_UNKNOWN and value == 0:
        return None     # UPower 啟動與結束時寫入的「0 % 狀態不明」邊界假點
    return UpowerPoint(t, float(value), state)


def parse_points(raw, now):
    """GetHistory 的回覆 → 依時間排序、時間不重複的 UpowerPoint；只留最新的 MAX_POINTS 點。"""
    if not isinstance(raw, (list, tuple)):
        return ()
    points = sorted((point for point in (valid_point(entry, now) for entry in raw)
                     if point is not None), key=lambda point: point.t)
    unique = [point for index, point in enumerate(points)
              if index + 1 == len(points) or points[index + 1].t != point.t]
    return tuple(unique[-Backfill.MAX_POINTS:])


def battery_sample(point):
    if point.state in PLUGGED_STATES:
        plugged = True
    elif point.state in ON_BATTERY_STATES:
        plugged = False
    else:
        return None     # 不知道接電狀態，圖表無法分色
    return HistorySample(point.t, round_half_up(point.value), None, plugged,
                         point.state == STATE_CHARGING, None, None, imported=True)


def ups_sample(point):
    online = True if point.state in PLUGGED_STATES else (
        False if point.state in ON_BATTERY_STATES else None)
    # 與自己的桌機紀錄相同：不知道是否接市電時算接電
    return HistorySample(point.t, None, None, online is not False, False,
                         round(point.value, UPS_DECIMALS), online, imported=True)


def point_sample(series, point, now):
    """一點 → 補回的 HistorySample；狀態無法使用或結果無效時回傳 None。"""
    if series == SERIES_BATTERY:
        sample = battery_sample(point)
    elif series == SERIES_UPS:
        sample = ups_sample(point)
    else:
        return None
    return sample if sample is not None and valid_sample(sample, now) else None


# ── 空白與計畫 ─────────────────────────────────────────


def own_gaps(samples, now):
    """保留期限內沒有自己紀錄的時段（依時間排序）；samples 依時間排序。"""
    if not samples:
        return ()
    cutoff = now - History.RETENTION_SECONDS
    gaps = []
    if samples[0].t - cutoff > Backfill.MIN_GAP_SECONDS:
        gaps.append(Gap(cutoff, samples[0].t))
    for before, after in zip(samples, samples[1:]):
        start = max(before.t, cutoff)
        if after.t - start > Backfill.MIN_GAP_SECONDS:
            gaps.append(Gap(start, after.t))
    return tuple(gaps)


def kept_series(samples):
    """本程式目前有在記錄的系列：最新一筆自己的紀錄有值的系列。"""
    if not samples:
        return ()
    return tuple(series for series in SERIES if getattr(samples[-1], series) is not None)


def handled(gap, spans, series):
    return any(span.series == series and span.start <= gap.start and gap.end <= span.end
               for span in spans)


def plan_backfill(samples, imported, now, skipped=()):
    """還沒處理的空白與要讀的系列；時鐘落後（最新一筆在未來）時不補。"""
    if not samples or samples[-1].t > now + History.FUTURE_TOLERANCE_SECONDS:
        return Plan()
    gaps = own_gaps(samples, now)
    requests = []
    for series in kept_series(samples):
        if series in skipped:
            continue
        pending = tuple(gap for gap in gaps if not handled(gap, imported.spans, series))
        if pending:
            requests.append(Request(series, pending))
    if not requests:
        return Plan()
    earliest = min(request.gaps[0].start for request in requests)
    timespan = min(History.RETENTION_SECONDS, math.ceil(now - earliest))
    return Plan(tuple(requests), timespan)


# ── 篩選與合併 ─────────────────────────────────────────


def matches(points, samples, series):
    """UPower 在自己最新一筆紀錄當時的電量與該筆相差不超過容許值；沒有可比較的值時視為相符。"""
    own = [sample for sample in samples if getattr(sample, series) is not None]
    if not own:
        return True
    latest = own[-1]
    earlier = [point for point in points if point.t <= latest.t]
    if not earlier:
        return True
    return abs(earlier[-1].value - getattr(latest, series)) <= Backfill.MATCH_TOLERANCE_PCT


def inside(t, gaps):
    edge = Backfill.EDGE_SECONDS
    return any(gap.start + edge < t < gap.end - edge for gap in gaps)


def accept(series, points, gaps, now):
    """空白內（兩端各留 60 秒）可用的點 → 補回的樣本；只留最新的 MAX_POINTS 筆。"""
    samples = [point_sample(series, point, now) for point in points if inside(point.t, gaps)]
    return tuple(sample for sample in samples if sample is not None)[-Backfill.MAX_POINTS:]


def trim_imported(imported, now):
    """修剪到保留期限並套用筆數上限（都保留最新的）。"""
    cutoff = now - History.RETENTION_SECONDS
    samples = trim(imported.samples, now)[-Backfill.MAX_STORED:]
    spans = sorted((span for span in imported.spans if span.end >= cutoff),
                   key=lambda span: span.end)
    return Imported(tuple(samples), tuple(spans[-Backfill.MAX_SPANS:]))


def merge_imported(imported, series, gaps, samples, now):
    """加入補回的樣本，並把這些空白記成這個系列已處理（即使沒有任何一點）。"""
    merged = sorted(imported.samples + tuple(samples), key=lambda sample: sample.t)
    spans = imported.spans + tuple(Span(series, gap.start, gap.end) for gap in gaps)
    return trim_imported(Imported(tuple(merged), spans), now)


# ── 檔案欄位 ─────────────────────────────────────────


def imported_section(imported):
    """history.json 的 "imported" 欄位；沒有任何補回與處理過的空白時回傳 None（不寫欄位）。"""
    if not imported.samples and not imported.spans:
        return None
    spans = {}
    for span in imported.spans:
        spans.setdefault(span.series, []).append([span.start, span.end])
    return {"samples": [sample.to_row() for sample in imported.samples], "spans": spans}


def imported_row(row):
    """補回的列：欄位有效、沒有耗電，而且只有電池或不斷電系統其中一個電量。"""
    sample = sample_from_row(row)
    if sample is None or sample.watts is not None or \
            (sample.pct is None) == (sample.ups_pct is None):
        raise InvalidImported("invalid row")
    return replace(sample, imported=True)


def decode_spans(raw):
    if not isinstance(raw, dict) or not set(raw) <= set(SERIES):
        raise InvalidImported("invalid spans")
    spans = []
    for series, ranges in raw.items():
        if not isinstance(ranges, list):
            raise InvalidImported("invalid spans")
        for item in ranges:
            if not (isinstance(item, list) and len(item) == 2 and all(map(is_real, item))
                    and 0 < item[0] < item[1]):
                raise InvalidImported("invalid span")
            spans.append(Span(series, item[0], item[1]))
    return tuple(sorted(spans, key=lambda span: span.end))


def decode_imported(raw):
    """檔案的 "imported" 欄位 → Imported；沒有欄位時為空，任何部分無效就拋出 InvalidImported。"""
    if raw is None:
        return Imported()
    if not isinstance(raw, dict) or not isinstance(raw.get("samples"), list):
        raise InvalidImported("unexpected structure")
    samples = tuple(sorted((imported_row(row) for row in raw["samples"]),
                           key=lambda sample: sample.t))
    return Imported(samples, decode_spans(raw.get("spans")))
