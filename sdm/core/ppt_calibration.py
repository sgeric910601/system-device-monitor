"""AMD APU 的 PPT 校正（不載入 GTK）：確認 ppt_raw ÷ 1000 真的是處理器插槽的瓦數。

amdgpu 的 power1（標籤 PPT）在 APU 上的單位沒有文件可查。RAPL 取樣開啟期間，每次耗電取樣
同時有 RAPL package 瓦數與 PPT 讀值時記下比例（ppt_raw ÷ 1000）÷ RAPL；每累積 30 筆
算一次中位數，在 ±25% 內就記為「已確認」，否則為「未確認」。結果有改變時才寫入
XDG 狀態目錄的 cpu-calibration.json（{"format":1,"ppt":{"scale":1000,"verified":true}}）。
之後 RAPL 關閉時，只有已確認的校正才會把 PPT 當作 CPU（名稱標明「插槽功耗估算」）；
從來不在未確認時把 PPT 顯示成 CPU。
"""
from collections import deque
from dataclasses import dataclass
import logging
import math
import statistics

from sdm.constants import CpuPower
from sdm.core.jsonfile import load_json, write_json_atomic

LOG = logging.getLogger(__name__)
FORMAT = 1


@dataclass(frozen=True)
class Calibration:
    scale: int = CpuPower.PPT_SCALE
    verified: bool = False


def parse_state(value):
    """狀態檔內容 → Calibration；格式、倍率或旗標不符時一律視為未確認。"""
    ppt = value.get("ppt") if isinstance(value, dict) and value.get("format") == FORMAT else None
    if not isinstance(ppt, dict) or ppt.get("scale") != CpuPower.PPT_SCALE \
            or type(ppt.get("verified")) is not bool:
        return Calibration()
    return Calibration(CpuPower.PPT_SCALE, ppt["verified"])


def state_value(verified):
    return {"format": FORMAT, "ppt": {"scale": CpuPower.PPT_SCALE, "verified": verified}}


def load_state(path):
    return load_json(path)


def save_state(path, value):
    try:
        write_json_atomic(path, value)
    except OSError as error:
        LOG.warning("Cannot save the PPT calibration: %s", type(error).__name__)


def usable(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value < high


class PptCalibration:
    """observe() 在收集執行緒中（耗電服務的鎖內）呼叫；save(value) 只在結果改變時呼叫。"""

    def __init__(self, load=lambda: None, save=lambda value: None,
                 samples=CpuPower.CALIBRATION_SAMPLES, tolerance=CpuPower.CALIBRATION_TOLERANCE):
        self.calibration = parse_state(load())
        self.save, self.tolerance = save, tolerance
        self.ratios = deque(maxlen=samples)

    @property
    def verified(self):
        return self.calibration.verified

    def observe(self, ppt_raw, rapl_w):
        if not usable(rapl_w, CpuPower.CALIBRATION_MIN_W, CpuPower.CALIBRATION_MAX_W) \
                or not usable(ppt_raw, 0, math.inf):
            return
        self.ratios.append(ppt_raw / self.calibration.scale / rapl_w)
        if len(self.ratios) < self.ratios.maxlen:
            return
        verified = abs(statistics.median(self.ratios) - 1) <= self.tolerance
        self.ratios.clear()
        if verified != self.calibration.verified:
            self.calibration = Calibration(CpuPower.PPT_SCALE, verified)
            LOG.info("PPT calibration %s", "verified" if verified else "rejected")
            self.save(state_value(verified))

    def watts(self, ppt_raw):
        """已確認時回傳 PPT 換算的瓦數（四捨五入到 0.1 W），否則 None。"""
        if not self.verified or not usable(ppt_raw, 0, math.inf):
            return None
        watts = round(ppt_raw / self.calibration.scale, CpuPower.WATTS_DIGITS)
        return watts if watts < CpuPower.CALIBRATION_MAX_W else None
