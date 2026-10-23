"""桌面寵物與大頭照的動作（純邏輯，移植參考專案 DesktopPet 與 CatEyes）。

- 摸牠：滑鼠在寵物上左右來回，每次換方向記一次（移動超過 2 px 才算），1.5 秒內換 3 次
  就算摸了一下；之後 1.2 秒內不再算，避免一直冒愛心。
- 眨眼：每 2.5–6 秒一次，五次中有一次連眨兩下；每次閉眼、張眼各 0.09 秒。
- 眼睛：大頭照上兩隻眼睛的中心與半徑是照片邊長的比例；眼珠往游標的方向移，
  游標越遠移得越多，最多左右半徑的 0.3、上下 0.16。
"""
import math

from sdm.constants import Companion, Pet


class StrokeDetector:
    def __init__(self):
        self.last_x, self.direction = None, 0
        self.turns, self.last_petted = [], -math.inf

    def reset(self):
        """滑鼠離開寵物：重新開始計算（方向保留，與參考專案相同）。"""
        self.last_x, self.turns = None, []

    def feed(self, x, now):
        """滑鼠移到 x（視窗座標）；這次移動完成「摸一下」時回傳 True。"""
        last, self.last_x = self.last_x, x
        if last is None or abs(x - last) <= Pet.STROKE_MIN_PX:
            return False
        direction = 1 if x > last else -1
        if self.direction and direction != self.direction:
            self.turns.append(now)
        self.direction = direction
        self.turns = [turn for turn in self.turns if now - turn <= Pet.STROKE_WINDOW_SECONDS]
        if len(self.turns) >= Pet.STROKE_TURNS and \
                now - self.last_petted > Pet.STROKE_COOLDOWN_SECONDS:
            self.turns, self.last_petted = [], now
            return True
        return False


class BlinkScheduler:
    """亂數來源由外部提供（測試用固定種子）。"""

    def __init__(self, rng):
        self.rng = rng

    def next_delay(self):
        return self.rng.uniform(Companion.BLINK_MIN_SECONDS, Companion.BLINK_MAX_SECONDS)

    def twice(self):
        return self.rng.randrange(Companion.DOUBLE_BLINK_ONE_IN) == 0


def blink_seconds(twice):
    """一次（或連兩次）眨眼的總長度。"""
    return 2 * Companion.BLINK_HALF_SECONDS * (2 if twice else 1)


def lid_fraction(elapsed, twice):
    """眨眼開始後 elapsed 秒時眼皮蓋住的比例（0 張開、1 閉上）。"""
    if elapsed <= 0 or elapsed >= blink_seconds(twice):
        return 0.0
    half = Companion.BLINK_HALF_SECONDS
    phase = elapsed % (2 * half)
    return phase / half if phase <= half else (2 * half - phase) / half


def eye_circles(side, left=0.0, top=0.0, eyes=Companion.EYES):
    """照片畫在（left, top）、邊長 side 時，每隻眼睛的（中心 x、中心 y、半徑）像素。"""
    return tuple((left + x * side, top + y * side, radius * side) for x, y, radius in eyes)


def gaze(center, pointer, width):
    """眼珠的方向（-1～1）；x、y 都是往右、往下為正。游標離眼睛不到 1 px 時看前方。"""
    dx, dy = pointer[0] - center[0], pointer[1] - center[1]
    distance = math.hypot(dx, dy)
    if distance < 1:
        return 0.0, 0.0
    strength = min(1.0, distance / max(width * Companion.GAZE_RANGE_RATIO,
                                       Companion.GAZE_MIN_RANGE))
    return dx / distance * strength, dy / distance * strength


def iris_offset(direction, radius):
    return direction[0] * radius * Companion.GAZE_X, direction[1] * radius * Companion.GAZE_Y
