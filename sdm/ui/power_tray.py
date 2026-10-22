"""頂端列「耗電」項目的基本內容：文字、選單第一行，以及動態圖示寫不出來時的退回圖示
（桌機為 icons/power.svg，筆電為圖示主題的電池圖示）。動態圖示見 tray_icons。"""
from dataclasses import dataclass

from sdm import paths
from sdm.services.battery_service import LaptopPower
from sdm.ui import battery_text, power_text

DESKTOP_ICON = str(paths.ROOT/"icons"/"power.svg")


@dataclass(frozen=True)
class TrayPower:
    label: str      # 指示器文字，例如「288 W」或「82%」
    menu: str       # 選單第一行
    icon: str       # 圖示：桌機為 power.svg 的路徑，筆電為圖示主題的電池圖示名稱


def tray_power(power):
    if isinstance(power, LaptopPower):
        return TrayPower(battery_text.tray_label(power), battery_text.menu_line(power),
                         battery_text.tray_icon(power))
    return TrayPower(power_text.tray_label(power), power_text.menu_line(power), DESKTOP_ICON)
