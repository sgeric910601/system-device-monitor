"""通知的協調（不載入 GTK，只在主執行緒呼叫）：耗電快照與設備清單 → 狀態機 → 頻率限制 → 送出。

- 收集結果已經由 GLib.idle_add 交回主執行緒；這裡只保存狀態，除了使用者在設定中的變更之外
  不寫任何檔案。
- 每份耗電快照（依 sequence）只評估一次；筆電電池與不斷電系統共用一個閘門，設備清單另一個。
  閘門與頻率限制都用含睡眠時間的時鐘（CLOCK_BOOTTIME）：睡眠時單調時鐘不會前進，
  看不出剛喚醒，睡著的時間也不會算進每分鐘一則的限制。偵測到喚醒時丟掉等待中的通知。
- 筆電不再放電時丟掉等待中的低電量通知，拔掉電源時丟掉等待中的充飽通知。
- 收集失敗時的設備資料沒有 stamp，不評估；清單有錯誤訊息時不移除斷線的設備。
- 頻率限制期間內的通知等到期間結束才送出（只送最新的一則），計時器由外部提供：
  schedule(秒, 回呼) 回傳來源代號、cancel(代號) 取消。
- 每次主迴圈最多送出一則（defer＝GLib.idle_add 逐則送出），送出可能要等通知服務回應，
  一次送好幾則會讓介面卡住。send() 回傳 False（失敗）時不算送過，30 秒後再試，最多 5 次。
- 測試通知不受頻率限制（使用者自己按的）。
"""
import time

from sdm.core import alert_messages as messages
from sdm.core import alert_messages as kinds
from sdm.core.alerts import (BatteryInput, Gate, LaptopAlertState, RateLimit, UpsAlertState,
                             clear_pending, delivered, drop, evaluate_laptop, evaluate_ups,
                             failed, next_release, offer, pass_gate, release)
from sdm.core.battery import CHARGING, DISCHARGING, LIMIT_REACHED
from sdm.core.peripheral_alerts import (PeripheralAlertState, evaluate_peripherals,
                                        peripheral_readings, present_keys)


def boot_clock():
    """含睡眠時間的時鐘（秒）。"""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def battery_input(power):
    """筆電快照 → BatteryInput（用顯示狀態，與畫面一致）；桌機或讀不到電量時回傳 None。"""
    battery = getattr(power, "battery", None)
    if battery is None or battery.percent is None:
        return None
    state, weak = power.state, bool(power.estimates.draining_on_ac)
    return BatteryInput(percent=int(battery.percent), plugged=bool(battery.ac_online),
                        draining=state == DISCHARGING or weak, weak_charger=weak,
                        charging=state == CHARGING, limit_reached=state == LIMIT_REACHED,
                        limit=battery.limit)


class AlertService:
    def __init__(self, send, schedule, cancel, defer=None, clock=boot_clock):
        self.send, self.schedule, self.cancel = send, schedule, cancel
        self.defer = defer or (lambda callback: schedule(0, callback))
        self.clock = clock
        self.outbox, self.dispatching = [], None
        self.laptop, self.ups = LaptopAlertState(), UpsAlertState()
        self.peripherals = PeripheralAlertState()
        self.power_gate = self.devices_gate = Gate()
        self.limit, self.timer, self.last_sequence = RateLimit(), None, None

    def power(self, power, prefs):
        """新的耗電快照；同一份快照（sequence 相同）只評估一次。"""
        if power is None or power.sequence == self.last_sequence:
            return
        self.last_sequence = power.sequence
        self.power_gate, record_only = self.gate(self.power_gate)
        alerts = ()
        reading = battery_input(power)
        if reading is not None:
            self.drop_stale(reading)
            self.laptop, alerts = evaluate_laptop(self.laptop, reading, prefs, record_only)
        self.ups, more = evaluate_ups(self.ups, getattr(power, "ups", None), prefs, record_only)
        self.deliver(alerts + more)

    def devices(self, payload, prefs):
        """新的設備清單；收集失敗（沒有 stamp）時不評估。"""
        if "stamp" not in payload:
            return
        self.devices_gate, record_only = self.gate(self.devices_gate)
        self.peripherals, alerts = evaluate_peripherals(
            self.peripherals, peripheral_readings(payload), prefs.peripheral_thresholds,
            present_keys(payload), record_only)
        self.deliver(alerts)

    def gate(self, gate):
        """回傳（新閘門, 是否只記錄）；剛喚醒時丟掉等待中的通知。"""
        gate, record_only = pass_gate(gate, self.clock())
        if record_only:
            self.replace_limit(clear_pending(self.limit))
        return gate, record_only

    def drop_stale(self, reading):
        stale = set()
        if not reading.draining:
            stale.add(kinds.LOW_BATTERY)
        if not reading.plugged:
            stale.add(kinds.BATTERY_FULL)
        if stale:
            self.replace_limit(drop(self.limit, stale))

    def replace_limit(self, limit):
        """等待中的通知有變動時才重新排定計時器。"""
        changed = limit.pending != self.limit.pending or limit.retry != self.limit.retry
        self.limit = limit
        if changed:
            self.reschedule()

    def send_test(self):
        return self.send(messages.test_alert())

    def deliver(self, alerts):
        now = self.clock()
        for alert in alerts:
            self.limit, ready = offer(self.limit, alert, now)
            self.queue(ready)
        if alerts:
            self.reschedule()

    def queue(self, ready):
        self.outbox.extend(ready)
        if self.outbox and self.dispatching is None:
            self.dispatching = self.defer(self.dispatch)

    def dispatch(self):
        """送出一則；還有就留到下一次主迴圈（回傳 True）。"""
        if self.outbox:
            alert = self.outbox.pop(0)
            ok, now = self.send(alert), self.clock()
            self.limit = (delivered(self.limit, alert, now) if ok
                          else failed(self.limit, alert, now))
        if self.outbox:
            return True
        self.dispatching = None
        self.reschedule()
        return False

    def reschedule(self):
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None
        due = next_release(self.limit)
        if due is not None:
            self.timer = self.schedule(max(0.0, due - self.clock()), self.released)

    def released(self):
        """頻率限制期間結束：送出等待中的通知。"""
        self.timer = None
        self.limit, ready = release(self.limit, self.clock())
        self.queue(ready)
        self.reschedule()
        return False

    def stop(self):
        """結束：取消計時器與還沒送出的通知。"""
        for source in (self.timer, self.dispatching):
            if source is not None:
                self.cancel(source)
        self.timer = self.dispatching = None
        self.outbox = []
