"""電力流向動畫的控制（不載入 GTK）：何時播放、流速與每條流帶的相位。

- 只在流向圖顯示中、視窗看得到（沒有最小化、沒有被完全遮住）、省電模式沒有生效，
  而且至少有一條流帶有瓦數時播放；其他時候不留任何計時器，改畫靜態箭頭
  （參考專案省電模式的靜態流向圖）。
- 最多每秒 15 格；流速與 √W 成正比並設上限。
- 數值更新只改流速，相位接續（參考專案 AnimationTests：數值更新不重設動畫）；
  已經不存在的流帶從相位表移除。停止期間不累積時間，重新播放時不會跳動。
計時器由外部提供：add(毫秒, 回呼) 回傳來源代號、remove(代號)。
"""
import math
import time

from sdm.constants import Flow


def should_animate(mapped, window_visible, iconified, saving, moving=True):
    return mapped and window_visible and not iconified and not saving and moving


def dash_speed(watts):
    """流速（px／秒）；沒有瓦數或不是正值時不流動。"""
    if watts is None or watts <= 0:
        return 0
    return min(Flow.DASH_SPEED_MAX,
               Flow.DASH_SPEED_MIN + Flow.DASH_SPEED_PER_SQRT_W * math.sqrt(watts))


def advanced(phases, seconds, speeds):
    """回傳新的相位表：每條流帶依自己的流速前進，只保留 speeds 中還存在的流帶。"""
    return {key: (phases.get(key, 0.0) + seconds * speed) % Flow.DASH_PERIOD
            for key, speed in speeds.items()}


class FlowAnimator:
    def __init__(self, redraw, add, remove, clock=time.monotonic):
        self.redraw, self.add, self.remove, self.clock = redraw, add, remove, clock
        self.source, self.last = None, None
        self.speeds, self.phases = {}, {}

    @property
    def running(self):
        return self.source is not None

    @property
    def moving(self):
        """至少有一條流帶有流速；全部是 0 時畫靜態箭頭就好。"""
        return any(speed > 0 for speed in self.speeds.values())

    def set_speeds(self, speeds):
        self.speeds = dict(speeds)
        self.phases = {key: value for key, value in self.phases.items() if key in self.speeds}

    def set_running(self, running):
        if running == self.running:
            return
        if running:
            self.last = self.clock()
            self.source = self.add(Flow.FRAME_MS, self.tick)
            return
        self.remove(self.source)
        self.source, self.last = None, None
        self.redraw()           # 換成靜態箭頭

    def tick(self):
        now = self.clock()
        elapsed = max(0.0, now - self.last) if self.last is not None else 0.0
        self.last = now
        self.phases = advanced(self.phases, elapsed, self.speeds)
        self.redraw()
        return True
