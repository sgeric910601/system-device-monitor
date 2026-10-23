"""APC 不斷電系統（apcupsd 的 apcaccess）唯讀讀取。

負載瓦數 = LOADPCT × NOMPOWER ÷ 100，是不斷電系統的總輸出，
包含螢幕等所有插在上面的設備，不只是電腦本身。
"""
from dataclasses import dataclass
import logging
import subprocess
from typing import Optional

from sdm.constants import Collect, Ups
from sdm.core.devices import parse_apc
from sdm.core.fmt import clean_text, number
from sdm.data.sources import command

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpsReading:
    """單次 apcaccess 讀值；讀不到或超出範圍的數值為 None。"""
    status: tuple = ()
    online: bool = False
    on_battery: bool = False
    low_battery: bool = False
    comm_lost: bool = False
    charge_pct: Optional[float] = None
    load_pct: Optional[float] = None
    nominal_w: Optional[float] = None
    load_w: Optional[float] = None
    time_left_min: Optional[float] = None
    line_v: Optional[float] = None
    model: str = ""


def bounded(text, low, high):
    """解析第一個數字；不是有限值或不在 low–high（含）之間時回傳 None。"""
    value = number(text)
    return value if value is not None and low <= value <= high else None


def parse_ups(text):
    """把 apcaccess status 的輸出轉成 UpsReading；沒有 STATUS 欄位時回傳 None。"""
    fields = parse_apc(text)
    if "STATUS" not in fields:
        return None
    flags = tuple(fields["STATUS"].split())
    model = clean_text(fields.get("MODEL", ""), Ups.MODEL_MAX_CHARS)
    if "COMMLOST" in flags:
        return UpsReading(status=flags, comm_lost=True, model=model)
    load = bounded(fields.get("LOADPCT"), 0, Ups.LOAD_MAX_PCT)
    nominal = bounded(fields.get("NOMPOWER"), Ups.NOMINAL_MIN_W, Ups.NOMINAL_MAX_W)
    return UpsReading(
        status=flags, online="ONLINE" in flags, on_battery="ONBATT" in flags,
        low_battery="LOWBATT" in flags,
        charge_pct=bounded(fields.get("BCHARGE"), 0, Ups.CHARGE_MAX_PCT),
        load_pct=load, nominal_w=nominal,
        load_w=load * nominal / 100 if load is not None and nominal is not None else None,
        time_left_min=bounded(fields.get("TIMELEFT"), 0, Ups.TIME_LEFT_MAX_MIN),
        line_v=bounded(fields.get("LINEV"), 0, Ups.LINE_V_MAX), model=model)


def read_ups(run=command):
    """執行 apcaccess；沒有安裝、服務未執行或逾時時回傳 None。"""
    try:
        return parse_ups(run([Collect.APCACCESS, "status"], Collect.APCACCESS_TIMEOUT))
    except (OSError, subprocess.SubprocessError) as error:
        LOG.debug("UPS status unavailable: %s", type(error).__name__)
        return None
