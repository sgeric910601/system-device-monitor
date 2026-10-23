"""充電夥伴動畫的控制與運動曲線（純邏輯，不載入 GTK）。

- 只在夥伴顯示中、所在的視窗看得到、省電模式沒有生效時播放；其他時候不留任何計時器，
  畫第一格的靜態圖（從靜態圖開始播放，不會跳動）。
- 每秒最多 12 格（Companion.MAX_FPS），實際使用 Companion.FPS。
- 呼吸：每 4 秒一次，以腳底為基準放大 1.00–1.02 倍，同時稍微往上浮。
- 休息時頭上飄出 Z（往上飄、變大、淡出），充電時充電墊周圍有火花閃爍。
- 預先算好的畫格：依原始影片每秒 15 格的速度循環，實際畫出的格數仍受上面的上限限制。
計時器由外部提供：add 以毫秒與回呼建立計時器並回傳代號，remove 以代號移除。
"""
import math
import time
from typing import NamedTuple

from sdm.constants import Companion

MILLISECONDS = 1000
ZZZ_RISE = 0.45                 # Z 往上飄的距離（夥伴高度的比例）
ZZZ_DRIFT = 0.12                # 往右飄的距離
ZZZ_SIZE = (0.10, 0.22)         # 由小變大（高度的比例）
SPARK_SPOTS = ((0.18, 0.80), (0.82, 0.78), (0.30, 0.95), (0.70, 0.96))


class Particle(NamedTuple):
    x: float                    # 夥伴寬度、高度的比例
    y: float
    size: float                 # 高度的比例
    alpha: float


def should_animate(mapped, window_visible, saving):
    return bool(mapped and window_visible and not saving)


def frame_interval_ms(fps):
    """計時器間隔（毫秒，無條件進位）：fps 再高也不超過每秒 MAX_FPS 格。"""
    return math.ceil(MILLISECONDS / min(fps, Companion.MAX_FPS))


class Ticker:
    """動畫的計時器：播放時只留一個計時器；停止時移除並重畫成靜態圖。"""

    def __init__(self, redraw, add, remove, fps=Companion.FPS, clock=time.monotonic):
        self.redraw, self.add, self.remove, self.clock = redraw, add, remove, clock
        self.interval = frame_interval_ms(fps)
        self.source, self.start, self.elapsed = None, None, 0.0

    @property
    def running(self):
        return self.source is not None

    def set_running(self, running):
        if running == self.running:
            return
        if running:
            self.start, self.elapsed = self.clock(), 0.0
            self.source = self.add(self.interval, self.tick)
            return
        self.remove(self.source)
        self.source, self.start, self.elapsed = None, None, 0.0
        self.redraw()

    def tick(self):
        self.elapsed = max(0.0, self.clock() - self.start)
        self.redraw()
        return True


def breath(t):
    """（放大倍數、往上浮的距離）；浮動距離是高度的比例，負值為往上。"""
    wave = (1 - math.cos(2 * math.pi * Companion.BREATH_HZ * t)) / 2
    return 1 + Companion.BREATH_SCALE * wave, -Companion.BOB_RATIO * wave


def zzz(t):
    """休息時的 Z：每個錯開三分之一個週期，從頭上往右上飄並淡出。"""
    particles = []
    for index in range(Companion.ZZZ_COUNT):
        phase = (t / Companion.ZZZ_PERIOD + index / Companion.ZZZ_COUNT) % 1.0
        alpha = min(1.0, phase * 4) * (1 - phase)
        size = ZZZ_SIZE[0] + (ZZZ_SIZE[1] - ZZZ_SIZE[0]) * phase
        particles.append(Particle(0.78 + ZZZ_DRIFT * phase, 0.42 - ZZZ_RISE * phase, size, alpha))
    return particles


def sparks(t):
    """充電時的火花：固定位置，各自錯開地亮起又熄滅。"""
    particles = []
    for index, (x, y) in enumerate(SPARK_SPOTS):
        phase = (t / Companion.SPARK_PERIOD + index / len(SPARK_SPOTS)) % 1.0
        alpha = math.sin(math.pi * phase) ** 2
        particles.append(Particle(x, y, 0.05 + 0.04 * alpha, alpha))
    return particles


def strip_index(t, count):
    """預先算好的畫格：t 秒時要畫第幾格（依原始影片的速度循環）。"""
    if count <= 0:
        return 0
    return int(t * Companion.FRAMES_SOURCE_FPS + 1e-9) % count
