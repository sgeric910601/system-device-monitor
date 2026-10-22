"""磁碟：型號清理、內建／外接與連線類型、容量長條顏色，以及 SMART 健康分級與文字。

SMART 資料來自 UDisks2（不需要管理員權限）：
- NVMe：健康度＝100−已用壽命（0～100），≥ 70% 綠色、≥ 30% 橘色、其他紅色；
  有嚴重警告或備用區塊低於門檻時顯示「異常，建議立即備份」（參考專案 DiskLifetime）。
- SATA：SMART 判定即將故障時顯示異常；有壞軌時顯示警告。
- SmartUpdated 為 0 時顯示「SMART 尚未更新」；兩種介面都沒有時顯示「SMART 不支援」。
"""
from dataclasses import dataclass
import re
from typing import NamedTuple, Optional
import unicodedata

from sdm.constants import Disk
from sdm.core import byte_format
from sdm.core.fmt import temperature

NVME, ATA, NONE = "nvme", "ata", "none"
TONE_NORMAL, TONE_GOOD, TONE_WARNING, TONE_CRITICAL = "normal", "good", "warning", "critical"
EXTERNAL_LABEL = "外接磁碟"
EXTERNAL_TRANSPORTS = ("usb", "mmc")
CONNECTIONS = {"nvme": "NVMe", "sata": "SATA", "ata": "SATA", "usb": "USB", "mmc": "SD"}
OTHER_CONNECTION = "其他"
BLOCK_NAME = re.compile(r"[A-Za-z0-9!._-]+")    # 核心把名稱裡的「/」寫成「!」（例如 cciss!c0d0）
STRIPPED_CATEGORIES = ("Cc", "Cf")      # 控制字元與雙向文字等格式字元
HOURS_PER_DAY = 24
PERCENT = 100
FAILING = "異常，建議立即備份"
LOADING = "讀取中…"


def sanitize_model(raw, fallback=EXTERNAL_LABEL):
    """磁碟型號由裝置本身決定：去掉控制字元與雙向文字控制符，最多 64 字。"""
    cleaned = "".join(char for char in str(raw or "")
                      if unicodedata.category(char) not in STRIPPED_CATEGORIES).strip()
    return cleaned[:Disk.MODEL_MAX_CHARS] if cleaned else fallback


def valid_block_name(name):
    """核心的區塊裝置名稱（sda、nvme0n1、dm-0、cciss!c0d0）：ASCII 英數字與 ! - _ .，
    不接受「/」「..」與開頭的「.」，避免路徑穿越。"""
    if not isinstance(name, str) or not name.isascii() or len(name) > Disk.BLOCK_NAME_MAX_CHARS:
        return False
    return bool(BLOCK_NAME.fullmatch(name)) and ".." not in name and not name.startswith(".")


def is_external(rm, hotplug, tran):
    """可移除、熱插拔，或接在 USB／SD 讀卡機上的磁碟算外接。"""
    return bool(rm) or bool(hotplug) or (tran or "") in EXTERNAL_TRANSPORTS


def connection_label(tran):
    return CONNECTIONS.get((tran or "").lower(), OTHER_CONNECTION)


def bar_fraction(used, total):
    if not total or total <= 0:
        return 0.0
    return min(1.0, max(0.0, used / total))


def bar_tone(fraction):
    if fraction > Disk.BAR_CRITICAL:
        return TONE_CRITICAL
    return TONE_WARNING if fraction > Disk.BAR_WARNING else TONE_NORMAL


def health_tone(percent):
    if percent >= Disk.HEALTH_GOOD:
        return TONE_GOOD
    return TONE_WARNING if percent >= Disk.HEALTH_FAIR else TONE_CRITICAL


def power_on_text(hours):
    """「2306 小時（約 96 天）」；少於 48 小時只顯示小時。"""
    text = f"{hours} 小時"
    return text + f"（約 {hours // HOURS_PER_DAY} 天）" if hours >= Disk.DAYS_FROM_HOURS else text


def kelvin_to_celsius(kelvin):
    """UDisks2 的溫度以 K 為單位，0 代表不知道；換算後不合理時回傳 None。"""
    if not isinstance(kelvin, (int, float)) or isinstance(kelvin, bool) or kelvin <= 0:
        return None
    celsius = kelvin - Disk.KELVIN_OFFSET
    return celsius if -Disk.KELVIN_OFFSET < celsius < 200 else None


@dataclass(frozen=True)
class DiskSmart:
    """一顆磁碟的 SMART 讀值（UDisks2）；讀不到的欄位為 None。"""
    kind: str = NONE
    updated: Optional[int] = None           # 最近一次 SMART 更新的 Unix 時間；0 代表尚未更新
    supported: Optional[bool] = None        # SATA
    enabled: Optional[bool] = None
    failing: Optional[bool] = None
    bad_sectors: Optional[int] = None
    critical_warning: tuple = ()            # NVMe 嚴重警告（例如 spare、temperature）
    power_on_hours: Optional[int] = None
    temperature_c: Optional[float] = None
    percent_used: Optional[int] = None      # NVMe 已用壽命（可能超過 100）
    avail_spare: Optional[int] = None
    spare_thresh: Optional[int] = None
    bytes_read: Optional[int] = None        # 出廠以來
    bytes_written: Optional[int] = None
    media_errors: Optional[int] = None

    @property
    def health_percent(self):
        if self.percent_used is None:
            return None
        return min(PERCENT, max(0, PERCENT - self.percent_used))

    @property
    def failing_now(self):
        if self.kind == ATA:
            return bool(self.failing)
        spare_low = (self.avail_spare is not None and self.spare_thresh is not None
                     and self.avail_spare < self.spare_thresh)
        return bool(self.critical_warning) or spare_low


class HealthSummary(NamedTuple):
    badge: str
    tone: str
    lines: tuple                            # （名稱、值、色調）


def common_lines(smart):
    lines = []
    if smart.power_on_hours is not None:
        lines.append(("通電時間", power_on_text(smart.power_on_hours), TONE_NORMAL))
    if smart.temperature_c is not None:
        lines.append(("溫度", temperature(smart.temperature_c), TONE_NORMAL))
    return lines


def nvme_summary(smart):
    percent = smart.health_percent
    lines = [] if percent is None else [("健康度", f"{percent}%", health_tone(percent))]
    lines += common_lines(smart)
    for name, value in (("出廠以來讀取", smart.bytes_read), ("出廠以來寫入", smart.bytes_written)):
        if value is not None:
            lines.append((name, byte_format.size(value), TONE_NORMAL))
    if smart.media_errors:
        lines.append(("媒體錯誤", f"{smart.media_errors} 次", TONE_WARNING))
    if smart.failing_now:
        return HealthSummary(FAILING, TONE_CRITICAL, tuple(lines))
    if percent is None:
        return HealthSummary("SMART 正常", TONE_GOOD, tuple(lines))
    return HealthSummary(f"健康度 {percent}%", health_tone(percent), tuple(lines))


def ata_summary(smart):
    lines = common_lines(smart)
    if smart.bad_sectors:
        lines.append(("壞軌", f"{smart.bad_sectors} 個", TONE_WARNING))
    if smart.failing_now:
        return HealthSummary(FAILING, TONE_CRITICAL, tuple(lines))
    if smart.bad_sectors:
        return HealthSummary(f"有 {smart.bad_sectors} 個壞軌", TONE_WARNING, tuple(lines))
    return HealthSummary("SMART 正常", TONE_GOOD, tuple(lines))


def summarize(smart):
    """健康徽章文字、色調與 SMART 詳細列；還沒讀過時顯示「讀取中…」。"""
    if smart is None:
        return HealthSummary(LOADING, TONE_NORMAL, ())
    if smart.kind == NONE or (smart.kind == ATA and smart.supported is False):
        return HealthSummary("SMART 不支援", TONE_NORMAL, ())
    if smart.kind == ATA and smart.enabled is False:
        return HealthSummary("SMART 未啟用", TONE_NORMAL, ())
    if smart.updated == 0:
        return HealthSummary("SMART 尚未更新", TONE_NORMAL, tuple(common_lines(smart)))
    return nvme_summary(smart) if smart.kind == NVME else ata_summary(smart)
