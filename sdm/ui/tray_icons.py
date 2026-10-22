"""頂端列耗電圖示：電池、插頭與不斷電系統的 SVG（不載入 GTK，同樣的狀態一定產生同樣的內容）。

- 顏色沿用參考專案 StatusIconRenderer：電量 ≤ 低電量門檻且正在放電（含接電但供電不足）為紅色，
  接著電源為綠色，其他為白色；充電中疊上閃電，接電未充電、已充飽與已達上限疊上插頭。
- 「圖示內百分比＋剩餘時間」把百分比畫在電池裡；「只有百分比」不顯示圖示。
- 電池圖示寬度至少是高度的 1.5 倍：GNOME 的 AppIndicator 擴充功能會依原比例顯示寬圖示。
- 桌機：市電時為插頭；不斷電系統改用電池時改成橘色的不斷電系統圖示並畫出電量，電量低為紅色。
- IconWriter 把 SVG 寫到執行期目錄（資料夾 0700、檔案 0600），內容改變才寫，並在
  power-a.svg 與 power-b.svg 之間輪替：GNOME 依路徑快取圖示，同一個路徑不會重新載入。
"""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import stat
from typing import Optional

from sdm.core.battery import CHARGING, DISCHARGING
from sdm.core.jsonfile import write_bytes_atomic
from sdm.ui.theme import CRITICAL, GOOD, WARNING

LOG = logging.getLogger(__name__)
PANEL = "#ffffff"               # 頂端列一律是深色背景
OUTLINE_OPACITY = 0.55
NORMAL_OPACITY = 0.9
INLINE_OPACITY = 0.22           # 圖示內有百分比時，電量底色淡一些，文字才看得清楚
TONES = {"normal": PANEL, "low": CRITICAL, "plugged": GOOD}
HEIGHT = 16
WIDTH = 26
INLINE_WIDTH = 36
BODY_INSET = 2
FILL_MIN = 1.5
PERCENT = 100
FONT = "Ubuntu, Cantarell, sans-serif"
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
ICON_DISPLAY_NONE = "percent_only"
INLINE_DISPLAY = "icon_percent_time"


@dataclass(frozen=True)
class BatteryGlyph:
    percent: Optional[int]
    tone: str               # normal／low／plugged
    overlay: str            # ""／bolt／plug
    inline_percent: bool


@dataclass(frozen=True)
class DesktopGlyph:
    ups_on_battery: bool
    low: bool               # 不斷電系統回報電量低
    charge: Optional[int]   # 改用電池時的不斷電系統電量


def battery_glyph(power, display, low_threshold):
    percent, state = power.percent, power.state
    draining = state == DISCHARGING or power.estimates.draining_on_ac
    if percent is not None and draining and percent <= low_threshold:
        tone = "low"
    else:
        tone = "normal" if state == DISCHARGING else "plugged"
    overlay = "" if state == DISCHARGING else ("bolt" if state == CHARGING else "plug")
    return BatteryGlyph(percent, tone, overlay, display == INLINE_DISPLAY)


def desktop_glyph(power):
    ups = None if power is None else power.ups
    if ups is None or not ups.on_battery:
        return DesktopGlyph(False, False, None)
    charge = None if ups.charge_pct is None else int(round(ups.charge_pct))
    return DesktopGlyph(True, bool(ups.low_battery), charge)


def power_glyph(power, display, low_threshold):
    """要畫的圖示；「只有百分比」時回傳 None（不顯示圖示）。"""
    if display == ICON_DISPLAY_NONE:
        return None
    if getattr(power, "battery", None) is not None:
        return battery_glyph(power, display, low_threshold)
    return desktop_glyph(power)


def number(value):
    """固定格式的數字，讓輸出完全由輸入決定。"""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def svg(width, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{HEIGHT}" '
            f'viewBox="0 0 {width} {HEIGHT}">{body}</svg>')


def bolt(center):
    x = center
    points = ((x + 1.2, 3.4), (x - 3.2, 8.9), (x - 0.3, 8.9), (x - 1.2, 12.6), (x + 3.2, 7.1),
              (x + 0.3, 7.1))
    text = " ".join(f"{number(a)},{number(b)}" for a, b in points)
    return (f'<polygon id="bolt" points="{text}" fill="{PANEL}" stroke="#000000" '
            'stroke-opacity="0.45" stroke-width="0.6" stroke-linejoin="round"/>')


def plug(center):
    return (f'<g id="plug" fill="{PANEL}" stroke="#000000" stroke-opacity="0.45" '
            f'stroke-width="0.5"><rect x="{number(center - 2)}" y="4.2" width="1.1" height="2.6"/>'
            f'<rect x="{number(center + 0.9)}" y="4.2" width="1.1" height="2.6"/>'
            f'<rect x="{number(center - 3)}" y="6.6" width="6" height="3.4" rx="1.2"/>'
            f'<rect x="{number(center - 0.6)}" y="9.8" width="1.2" height="2.4"/></g>')


def render_battery(glyph):
    width = INLINE_WIDTH if glyph.inline_percent else WIDTH
    body_width = width - 3.5
    inner = body_width - 2 * BODY_INSET
    level = 0 if glyph.percent is None else min(PERCENT, max(0, glyph.percent)) / PERCENT
    fill = max(FILL_MIN, inner * level)
    opacity = INLINE_OPACITY if glyph.inline_percent else NORMAL_OPACITY
    center = 0.5 + body_width / 2
    parts = [
        f'<rect x="0.5" y="2.5" width="{number(body_width)}" height="11" rx="3" fill="none" '
        f'stroke="{PANEL}" stroke-opacity="{OUTLINE_OPACITY}"/>',
        f'<rect x="{number(body_width + 1.1)}" y="5.5" width="1.9" height="5" rx="0.8" '
        f'fill="{PANEL}" fill-opacity="{OUTLINE_OPACITY}"/>',
        f'<rect id="level" x="2.5" y="4.5" width="{number(fill)}" height="7" rx="1.5" '
        f'fill="{TONES[glyph.tone]}" fill-opacity="{opacity}"/>']
    if glyph.inline_percent:
        text = "—" if glyph.percent is None else f"{min(PERCENT, max(0, glyph.percent))}%"
        parts.append(f'<text x="{number(center)}" y="11.3" font-family="{FONT}" font-size="9" '
                     f'font-weight="bold" text-anchor="middle" fill="{PANEL}">{text}</text>')
    elif glyph.overlay == "bolt":
        parts.append(bolt(center))
    elif glyph.overlay == "plug":
        parts.append(plug(center))
    return svg(width, "".join(parts))


def render_desktop(glyph):
    if not glyph.ups_on_battery:
        body = (f'<g id="plug" fill="{PANEL}"><rect x="5" y="1" width="1.6" height="4" rx="0.6"/>'
                '<rect x="9.4" y="1" width="1.6" height="4" rx="0.6"/>'
                '<path d="M3.2 5h9.6v2.4a4.8 4.8 0 0 1 -9.6 0z"/>'
                '<rect x="7.2" y="11.6" width="1.6" height="3.4" rx="0.6"/></g>')
        return svg(HEIGHT, body)
    color = CRITICAL if glyph.low else WARNING
    level = 0 if glyph.charge is None else min(PERCENT, max(0, glyph.charge)) / PERCENT
    height = max(1.0, 10 * level)
    body = (f'<g id="ups"><rect x="2.5" y="1.5" width="11" height="13" rx="2" fill="none" '
            f'stroke="{color}" stroke-width="1.2"/>'
            f'<rect id="level" x="4.5" y="{number(13 - height)}" width="7" '
            f'height="{number(height)}" rx="1" fill="{color}" fill-opacity="0.85"/></g>')
    return svg(HEIGHT, body)


def render(glyph):
    if isinstance(glyph, BatteryGlyph):
        return render_battery(glyph)
    return render_desktop(glyph)


class IconWriter:
    def __init__(self, directory, name="power"):
        self.directory, self.name = Path(directory), name
        self.content, self.path, self.warned = None, None, False

    def publish(self, content):
        """寫好的檔案路徑（內容沒變時沿用上一個）；無法寫入時回傳 None，之後再試。"""
        if content == self.content and self.path is not None:
            return self.path
        letter = "b" if self.path is not None and self.path.stem.endswith("-a") else "a"
        target = self.directory/f"{self.name}-{letter}.svg"
        try:
            self.prepare()
            write_bytes_atomic(target, content.encode("utf-8"), FILE_MODE)
        except OSError as error:
            if not self.warned:
                LOG.warning("Cannot write the tray icon: %s", type(error).__name__)
                self.warned = True
            return None
        self.content, self.path, self.warned = content, target, False
        return target

    def prepare(self):
        """建立私人資料夾；已存在時必須是本人擁有的資料夾（不是符號連結）。"""
        for directory in (self.directory.parent, self.directory):
            directory.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
        info = os.lstat(self.directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("Icon directory is not a private directory")
        if stat.S_IMODE(info.st_mode) != DIRECTORY_MODE:
            os.chmod(self.directory, DIRECTORY_MODE)
