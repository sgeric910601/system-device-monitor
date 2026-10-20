"""累計計數器換算為速率與 CPU 使用率。"""
from sdm.constants import Counter


class Counters:
    def __init__(self):
        self.previous = {}

    def rate(self, key, value, now):
        previous = self.previous.get(key)
        self.previous[key] = (value, now)
        if previous is None:
            return None
        old, stamp = previous
        elapsed = now - stamp
        # 睡眠／喚醒、介面或計數器被替換時，需要重新建立基準。
        if elapsed <= 0 or elapsed > Counter.RESET_SECONDS or value < old:
            return None
        return (value - old) / elapsed


class CpuCounter:
    def __init__(self):
        self.previous = None

    def sample(self, values, now):
        total = sum(values[:8])  # guest 時間已含在 user／nice 內，不重複計算
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        old = self.previous
        self.previous = (total, idle, now)
        if old is None or now - old[2] > Counter.RESET_SECONDS:
            return None
        delta, idle_delta = total - old[0], idle - old[1]
        if delta <= 0 or idle_delta < 0:
            return None
        return max(0, min(100, (delta - idle_delta) / delta * 100))
