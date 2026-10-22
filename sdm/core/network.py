"""主要網路介面的收送速率（參考專案 NetworkMonitor）：主要介面改變時重新建立基準。"""
from sdm.core.counters import Counters


class PrimaryRates:
    def __init__(self):
        self.name, self.counters = None, Counters()

    def sample(self, name, rx_bytes, tx_bytes, now):
        """回傳（下載速率、上傳速率、主要介面是否改變）；第一次或剛換介面時速率為 None。"""
        changed = name != self.name
        if changed:
            self.name, self.counters = name, Counters()
        if name is None:
            return None, None, changed
        return (self.counters.rate("rx", rx_bytes, now), self.counters.rate("tx", tx_bytes, now),
                changed)
