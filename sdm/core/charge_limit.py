"""充電上限滑桿的純邏輯（移植參考專案 ChargeLimitLogic 與 SystemChargeLimit 的拖動規則）。

- options_from：整理可選的上限：只留 50–100 的整數、去重排序，一定要有 100（不限制）
  而且至少兩個值；否則用預設級距 80、85、90、95、100。
- normalized：讀到的上限超出 50–100（例如從沒設定過的 0）視為不限制（100）。
- index_of／limit_at：上限與滑桿格子互換；格子四捨五入（.5 遠離 0，與 Swift 的 rounded() 相同）
  後限制在範圍內，不是有限數字或沒有選項時為 None。
- refine_options：驅動不接受某個值（讀回的值不同）時，把它從選項移除（100 一定保留），
  並加入驅動實際套用的值；有些機型只接受固定幾個值（例如 60、80、100）。
- CommitDebouncer：拖動中只記下草稿，放開滑桿或停止 0.6 秒才送出一次；寫入中不送出，
  寫完後若又拖到別的位置，再送出最後的值。
"""
import math

from sdm.constants import ChargeLimit

DEFAULT_OPTIONS = ChargeLimit.OPTIONS
MIN_OPTIONS = 2


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def options_from(raw):
    """可選的上限（由小到大）；不合理時回傳預設級距。"""
    values = sorted({value for value in (raw or ()) if is_integer(value)
                     and ChargeLimit.MIN_LIMIT <= value <= ChargeLimit.MAX_LIMIT})
    if len(values) < MIN_OPTIONS or values[-1] != ChargeLimit.MAX_LIMIT:
        return DEFAULT_OPTIONS
    return tuple(values)


def normalized(raw):
    """讀到的上限；不在 50–100 之間（含讀不到）視為不限制。"""
    if is_integer(raw) and ChargeLimit.MIN_LIMIT <= raw <= ChargeLimit.MAX_LIMIT:
        return raw
    return ChargeLimit.MAX_LIMIT


def index_of(limit, options):
    """上限 → 最接近的格子；距離相同時取較小的格子，沒有選項時為 0。"""
    if not options:
        return 0
    return min(range(len(options)), key=lambda index: abs(options[index] - limit))


def round_away(value):
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def limit_at(position, options):
    """滑桿位置（第幾格，可以有小數）→ 上限。"""
    if not options or not isinstance(position, (int, float)) or not math.isfinite(position):
        return None
    index = min(max(round_away(position), 0), len(options) - 1)
    return options[index]


def nearest(value, options):
    return options[index_of(value, options)] if options else None


def refine_options(options, refused, applied):
    """驅動沒有接受 refused、實際套用了 applied 之後的選項。"""
    kept = [value for value in options if value == ChargeLimit.MAX_LIMIT or value != refused]
    if is_integer(applied) and ChargeLimit.MIN_LIMIT <= applied <= ChargeLimit.MAX_LIMIT:
        kept.append(applied)
    return options_from(kept)


class CommitDebouncer:
    """拖動的送出規則；schedule(秒, 回呼) 回傳來源代號，cancel(來源代號) 取消，
    commit(值) 開始寫入。寫入完成後由擁有者呼叫 finished(值)。"""

    def __init__(self, schedule, cancel, commit, delay=ChargeLimit.DEBOUNCE):
        self.schedule, self.cancel, self.commit, self.delay = schedule, cancel, commit, delay
        self.draft, self.editing, self.busy, self.timer = None, False, False, None

    def drag(self, value):
        if value == self.draft:
            return
        self.draft = value
        self.stop()
        self.timer = self.schedule(self.delay, self.expired)

    def set_editing(self, editing):
        self.editing = bool(editing)
        if not self.editing:
            self.flush()

    def expired(self):
        self.timer = None
        self.flush()
        return False

    def flush(self):
        if self.editing or self.busy or self.draft is None:
            return
        self.stop()
        self.busy = True
        self.commit(self.draft)

    def finished(self, value):
        """寫入結束（成功或失敗）：草稿就是剛寫的值時清除，否則送出新的草稿。"""
        self.busy = False
        if self.draft == value:
            self.draft = None
        else:
            self.flush()

    def clear(self):
        """不需要寫入（例如草稿等於目前的上限，或寫入失敗）：丟掉草稿。"""
        self.stop()
        self.draft, self.busy = None, False

    def stop(self):
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None
