"""通知的內容（純邏輯，不載入 GTK）：標題、內文、圖示、緊急程度與點通知時開啟的頁面。

用詞沿用參考專案 Notifier；圖示名稱取自系統圖示主題。
隱私：內文只有百分比、瓦數、分鐘數與設備名稱；設備名稱先清理控制字元，
再把像 MAC 或 IPv4 位址的部分換成 x，絕不放序號、位址或裝置路徑。
"""
from dataclasses import dataclass
import math

from sdm.constants import Alerts
from sdm.core.fmt import clean_text, mask_addresses

LOW_BATTERY = "low_battery"
BATTERY_FULL = "battery_full"
PERIPHERAL_LOW = "peripheral_low"
UPS_ON_BATTERY = "ups_on_battery"
UPS_ON_MAINS = "ups_on_mains"
UPS_LOW = "ups_low"
UPS_COMM_LOST = "ups_comm_lost"
TEST = "test"

POWER_PAGE = "power"
DEVICES_PAGE = "devices"
NAME_MAX_CHARS = Alerts.NAME_MAX_CHARS
UNNAMED = "周邊設備"
LOW_LEVEL_TEXT = "低電量"


@dataclass(frozen=True)
class Alert:
    kind: str
    title: str
    body: str
    icon: str
    page: str = POWER_PAGE      # 點通知時開啟的頁面
    critical: bool = False      # 緊急通知：只有不斷電系統改用電池與電量低
    device: str = ""            # 周邊設備的雜湊鍵（同一種通知依設備分別限制頻率）

    @property
    def key(self):
        """頻率限制的單位：同一種通知、同一台設備。"""
        return (self.kind, self.device)


def whole(value):
    """四捨五入到整數（Python 的 round 是銀行家捨入）。"""
    return int(math.floor(value + 0.5))


def safe_name(name):
    """通知中的設備名稱：清理控制字元、遮蔽位址並限制長度；空白時用「周邊設備」。"""
    text = mask_addresses(clean_text(name, NAME_MAX_CHARS * 2))[:NAME_MAX_CHARS].strip()
    return text or UNNAMED


def low_battery(percent, threshold, weak_charger):
    body = ("已接上電源，但充電器功率不足，電量仍在下降。" if weak_charger
            else f"電量已降到 {threshold}% 以下，建議接上電源。")
    return Alert(LOW_BATTERY, f"電量偏低：{percent}%", body, "battery-caution-symbolic")


def battery_full(percent, full_threshold, limit_reached, limit):
    """三種內文：到達充電上限、充飽（門檻 100%）、充到門檻。"""
    if limit_reached and limit is not None:
        body = f"已達充電上限 {limit}%，電池暫停充電。"
    elif full_threshold >= Alerts.FULL_PERCENT:
        body = "電池已充飽，可以拔掉電源。"
    else:
        body = f"已充到 {full_threshold}%，可以拔掉電源。"
    return Alert(BATTERY_FULL, f"充電完成：{percent}%", body, "battery-full-charged-symbolic")


def peripheral_low(reading, threshold):
    """reading 為 PeripheralReading（名稱已清理）；只有電量等級時標題不寫百分比。"""
    if reading.percent is None:
        title = f"{reading.name} 電量偏低"
        body = f"設備回報的電量等級為「{LOW_LEVEL_TEXT}」，建議充電。"
    else:
        title = f"{reading.name} 電量偏低：{reading.percent}%"
        body = f"已降到你設定的 {threshold}% 以下，建議充電。"
    return Alert(PERIPHERAL_LOW, title, body, reading.icon, page=DEVICES_PAGE,
                 device=reading.key)


def ups_details(reading):
    """「電量 99%，負載約 297 W，預估可用約 44 分鐘」；讀不到的項目省略。"""
    parts = []
    if reading.charge_pct is not None:
        parts.append(f"電量 {whole(reading.charge_pct)}%")
    if reading.load_w is not None:
        parts.append(f"負載約 {whole(reading.load_w)} W")
    if reading.time_left_min is not None:
        parts.append(f"預估可用約 {whole(reading.time_left_min)} 分鐘")
    if reading.load_w is None and reading.time_left_min is None:
        parts.append("負載與可用時間暫時讀不到")
    return "，".join(parts) + "。"


def ups_on_battery(reading):
    return Alert(UPS_ON_BATTERY, "不斷電系統改用電池供電", ups_details(reading),
                 "uninterruptible-power-supply-symbolic", critical=True)


def ups_on_mains(reading):
    charge = None if reading is None else reading.charge_pct
    body = "不斷電系統已改回市電供電。"
    if charge is not None:
        body += f"目前電量 {whole(charge)}%。"
    return Alert(UPS_ON_MAINS, "已恢復市電", body, "ac-adapter-symbolic")


def ups_low(reading, threshold):
    body = f"已降到你設定的 {threshold}% 以下"
    if reading.time_left_min is not None:
        body += f"，預估可用約 {whole(reading.time_left_min)} 分鐘"
    return Alert(UPS_LOW, f"不斷電系統電量偏低：{whole(reading.charge_pct)}%",
                 body + "。請儲存工作，準備關機。", "battery-caution-symbolic", critical=True)


def ups_comm_lost():
    return Alert(UPS_COMM_LOST, "不斷電系統通訊中斷",
                 "讀不到不斷電系統的狀態，請檢查連接線與 apcupsd 服務。",
                 "dialog-warning-symbolic")


def test_alert():
    return Alert(TEST, Alerts.APP_NAME, "通知功能正常。", "utilities-system-monitor")
