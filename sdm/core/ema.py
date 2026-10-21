"""近 3 分鐘平均：指數移動平均，τ = 180 秒，與參考專案 BatteryStore.ema 相同。"""
import math

from sdm.constants import Power


def ema(previous, value, dt, tau=Power.EMA_TAU_SECONDS):
    """第一個樣本或 dt ≤ 0 時直接採用新值；否則 alpha = 1 - exp(-dt / tau)。"""
    if previous is None or dt <= 0:
        return value
    alpha = 1 - math.exp(-dt / tau)
    return previous + alpha * (value - previous)


class Ema:
    """保存目前平均與上次更新時間。

    與參考專案的桌機平均相同：讀不到值時保留原平均，但時間照樣前進。
    """

    def __init__(self, tau=Power.EMA_TAU_SECONDS):
        self.tau = tau
        self.value = None
        self.stamp = None

    def update(self, value, now):
        dt = 0 if self.stamp is None else now - self.stamp
        if value is not None:
            self.value = ema(self.value, value, dt, self.tau)
        self.stamp = now
        return self.value

    def reset(self):
        self.value = None
        self.stamp = None
