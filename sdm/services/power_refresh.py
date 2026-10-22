"""插拔電源後的重新讀取排程（筆電模式）；計時器函式由呼叫端提供，方便測試。

插拔通知可能比核心的電池資料早到，所以變壓器事件立即讀一次，之後 +1、+3、+6 秒再讀
（與參考專案 powerSourceChanged 相同）；新的插拔事件會從頭重新排程。
系統電池本身的事件只立即讀一次；周邊（scope 為 Device）的事件交給設備頁處理。
沒有收到變壓器事件、但兩次取樣之間接電狀態改變時，也補排後續的重新讀取。
"""
from sdm.constants import Battery

ADAPTER_TYPES = ("Mains", "USB")
BATTERY_TYPE = "Battery"
SCOPE_DEVICE = "Device"
NOW = 0


def refresh_delays(supply_type, scope):
    """power_supply 事件 → 要在哪些時間（秒）重新讀取；不需要時回傳空 tuple。"""
    if scope == SCOPE_DEVICE:
        return ()
    if supply_type in ADAPTER_TYPES:
        return (NOW,) + Battery.REFRESH_DELAYS
    return (NOW,) if supply_type == BATTERY_TYPE else ()


class PowerRefresh:
    def __init__(self, refresh, schedule, cancel):
        """schedule(秒, callback) 回傳計時器代號；callback 回傳 False 代表不再重複。"""
        self.refresh, self.schedule, self.cancel = refresh, schedule, cancel
        self.pending = []
        self.last_online = None

    def on_event(self, supply_type, scope):
        delays = refresh_delays(supply_type, scope)
        if len(delays) > 1:
            self.start(delays)
        elif delays:
            self.refresh()

    def observe(self, ac_online):
        """每次收到筆電快照時呼叫；接電狀態改變且沒有排程中的重讀時補排。"""
        changed = self.last_online is not None and ac_online != self.last_online
        self.last_online = ac_online
        if changed and not self.pending:
            self.start(Battery.REFRESH_DELAYS)

    def start(self, delays):
        self.stop()
        for delay in delays:
            if delay == NOW:
                self.refresh()
            else:
                self._later(delay)

    def _later(self, delay):
        token = object()

        def fire():
            self.pending = [entry for entry in self.pending if entry[0] is not token]
            self.refresh()
            return False

        self.pending.append((token, self.schedule(delay, fire)))

    def stop(self):
        for _, source in self.pending:
            self.cancel(source)
        self.pending = []
