"""通知的狀態機（純邏輯，不載入 GTK）：筆電低電量與充飽、不斷電系統、啟動與喚醒閘門、頻率限制。

每個狀態都是不可變的資料類別；轉移函式回傳（新狀態, 要送出的通知）。
規則沿用參考專案 Notifier.evaluate：
- 跨過門檻只通知一次，離開門檻一段距離後才重新啟用（遲滯）；關閉提醒時照樣記錄狀態，
  之後再打開不會立刻補發。
- record_only：啟動後第一次評估，以及偵測到喚醒（兩次取樣相隔超過 2 分鐘，含睡眠時間）
  後的那一次，只記錄目前狀態、不通知；登入或喚醒時不會跳出「充電完成」或「改用電池」。
- 低電量：正在放電（或接著電源但電池仍在放電）且電量 ≤ 門檻時通知；
  不再放電或電量回到門檻＋2% 以上才重新啟用。
- 充飽：接著電源且電量 ≥ 門檻（或已達充電上限），而且這次接上電源後看過「充電中」才通知；
  拔掉電源或電量低於 min(門檻, 上限) − 5% 才重新啟用。
- 不斷電系統：連續兩次取樣都在用電池才通知（緊急）；送出過這則通知，恢復市電時才通知
  「已恢復市電」。確認停電後電量 ≤ 門檻再通知一次（緊急），電量回到門檻＋2% 以上才重新啟用。
  通訊曾經正常、之後變成 COMMLOST 時通知一次，通訊恢復後重新啟用。
- 頻率限制：同一種通知（周邊為同一台設備）每 60 秒最多一則；期間內再來的只保留最新的一則，
  時間到才送出。只有送出成功（delivered）才算送過；失敗（failed，例如沒有通知服務）
  30 秒後再試，同一則最多試 5 次。
- 等待中的通知過時就丟掉：恢復市電時丟掉等待中的「電量偏低」，等待中的「改用電池」還沒顯示過時
  連同「已恢復市電」一起丟掉（停電與恢復都不顯示）；看板偵測到喚醒時丟掉全部等待中的通知；
  筆電不再放電、拔掉電源時由協調層丟掉等待中的低電量、充飽通知（drop()）。
"""
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping, Optional

from sdm.constants import Alerts
from sdm.core import alert_messages as messages

EMPTY = MappingProxyType({})
# 送出這種通知時，等待中的這些通知已經過時
SUPERSEDES = {messages.UPS_ON_MAINS: (messages.UPS_ON_BATTERY, messages.UPS_LOW)}
# 這一對中的前者還沒顯示過（仍在等待）就被取代時，後者也不顯示
PAIRS = {messages.UPS_ON_MAINS: messages.UPS_ON_BATTERY}


@dataclass(frozen=True)
class Gate:
    """上一次取樣的時間（含睡眠時間的時鐘）；None 代表還沒取樣過。"""
    last: Optional[float] = None


def pass_gate(gate, now):
    """回傳（新閘門, 這次是否只記錄）：第一次、相隔超過 2 分鐘或時鐘倒退時只記錄。"""
    record_only = gate.last is None or not 0 <= now - gate.last <= Alerts.RESUME_GAP_SECONDS
    return Gate(now), record_only


@dataclass(frozen=True)
class BatteryInput:
    """筆電電池在某次取樣的狀態（顯示狀態，與畫面一致）。"""
    percent: int
    plugged: bool
    draining: bool          # 放電中，或接著電源但電池仍在放電
    weak_charger: bool      # 接著電源但充電器功率不足
    charging: bool          # 顯示為充電中
    limit_reached: bool     # 顯示為已達充電上限
    limit: Optional[int]    # 充電上限（沒有設定時為 None）


@dataclass(frozen=True)
class LaptopAlertState:
    low_fired: bool = False
    full_fired: bool = False
    saw_charging: bool = False      # 這次接上電源後是否看過「充電中」


def low_now(reading, prefs):
    return reading.draining and reading.percent <= prefs.low_threshold


def full_now(reading, prefs):
    return reading.plugged and (reading.percent >= prefs.full_threshold or reading.limit_reached)


def step_low(fired, reading, prefs):
    if low_now(reading, prefs):
        due = prefs.low_alert_enabled and not fired
        return True, (messages.low_battery(reading.percent, prefs.low_threshold,
                                           reading.weak_charger),) if due else ()
    if not reading.draining or reading.percent > prefs.low_threshold + Alerts.REARM_MARGIN:
        return False, ()
    return fired, ()


def step_full(fired, saw_charging, reading, prefs):
    limit = Alerts.FULL_PERCENT if reading.limit is None else reading.limit
    target = min(prefs.full_threshold, limit)
    if full_now(reading, prefs):
        due = prefs.full_alert_enabled and not fired and saw_charging
        alert = messages.battery_full(reading.percent, prefs.full_threshold,
                                      reading.limit_reached, reading.limit)
        return True, (alert,) if due else ()
    if not reading.plugged or reading.percent < target - Alerts.FULL_REARM_MARGIN:
        return False, ()
    return fired, ()


def evaluate_laptop(state, reading, prefs, record_only=False):
    """prefs 需要 low_alert_enabled、low_threshold、full_alert_enabled、full_threshold。"""
    saw = (state.saw_charging and reading.plugged) or reading.charging
    if record_only:
        return LaptopAlertState(low_now(reading, prefs), full_now(reading, prefs), saw), ()
    low_fired, low = step_low(state.low_fired, reading, prefs)
    full_fired, full = step_full(state.full_fired, saw, reading, prefs)
    return LaptopAlertState(low_fired, full_fired, saw), low + full


@dataclass(frozen=True)
class UpsAlertState:
    battery_samples: int = 0        # 連續幾次取樣都在用電池
    outage_handled: bool = False    # 這次停電已處理過（已通知，或啟動／喚醒時就在停電）
    battery_notified: bool = False  # 送出過「改用電池」：恢復市電時要通知
    low_fired: bool = False
    seen: bool = False              # 曾經通訊正常
    comm_fired: bool = False


def step_ups_low(fired, reading, prefs, may_fire, record_only):
    """電量回到門檻＋2% 以上重新啟用；用電池且電量 ≤ 門檻時通知（停電確認後）。"""
    charge, threshold = reading.charge_pct, prefs.ups_low_threshold
    if charge is None:
        return fired, ()
    if charge > threshold + Alerts.REARM_MARGIN:
        return False, ()
    if not (reading.on_battery and charge <= threshold):
        return fired, ()
    if record_only:
        return True, ()
    if not may_fire:
        return fired, ()
    due = prefs.ups_alerts_enabled and not fired
    return True, (messages.ups_low(reading, threshold),) if due else ()


def on_battery(state, reading, prefs, record_only):
    samples = state.battery_samples + 1
    confirmed = samples >= Alerts.UPS_CONFIRM_SAMPLES
    handled, notified, alerts = state.outage_handled, state.battery_notified, ()
    if record_only:
        handled = True
    elif confirmed and not handled:
        handled = True
        if prefs.ups_alerts_enabled:
            alerts, notified = (messages.ups_on_battery(reading),), True
    low_fired, low = step_ups_low(state.low_fired, reading, prefs, confirmed, record_only)
    return replace(state, battery_samples=samples, outage_handled=handled,
                   battery_notified=notified, low_fired=low_fired), alerts + low


def on_mains(state, reading, prefs, record_only):
    due = state.battery_notified and prefs.ups_alerts_enabled and not record_only
    low_fired, _ = step_ups_low(state.low_fired, reading, prefs, False, record_only)
    return replace(state, battery_samples=0, outage_handled=False, battery_notified=False,
                   low_fired=low_fired), (messages.ups_on_mains(reading),) if due else ()


def comm_lost(state, prefs, record_only):
    if record_only:
        return replace(state, comm_fired=True), ()
    if not state.seen or state.comm_fired:
        return state, ()
    alerts = (messages.ups_comm_lost(),) if prefs.ups_alerts_enabled else ()
    return replace(state, comm_fired=True), alerts


def evaluate_ups(state, reading, prefs, record_only=False):
    """reading 為 UpsReading 或 None（讀不到 apcaccess：狀態不變）。

    prefs 需要 ups_alerts_enabled 與 ups_low_threshold。"""
    if reading is None:
        return state, ()
    if reading.comm_lost:
        return comm_lost(state, prefs, record_only)
    state = replace(state, seen=True, comm_fired=False)
    if reading.on_battery:
        return on_battery(state, reading, prefs, record_only)
    return on_mains(state, reading, prefs, record_only)


@dataclass(frozen=True)
class RateLimit:
    sent: Mapping = EMPTY       # 通知的鍵 → 上次成功送出的時間（秒）
    pending: Mapping = EMPTY    # 通知的鍵 → 等待中的通知（新的取代舊的）
    retry: Mapping = EMPTY      # 通知的鍵 →（失敗次數, 最早重試時間）
    inflight: frozenset = frozenset()   # 已交出去、還不知道成功與否的鍵


def frozen(mapping):
    return MappingProxyType(dict(mapping))


def ready_at(limit, key):
    """這個鍵最早可以再送出的時間：頻率限制與失敗後的重試間隔取較晚者。"""
    times = [limit.sent[key] + Alerts.RATE_LIMIT_SECONDS] if key in limit.sent else []
    if key in limit.retry:
        times.append(limit.retry[key][1])
    return max(times) if times else None


def allowed(limit, key, now):
    if key in limit.inflight:
        return False
    last = limit.sent.get(key)
    if last is not None and 0 <= now - last < Alerts.RATE_LIMIT_SECONDS:
        return False
    return key not in limit.retry or now >= limit.retry[key][1]


def pruned(limit, now):
    """只留還在限制期間內、等待中或正在送出的鍵，對照表不會一直變大。"""
    keep = set(limit.pending) | limit.inflight
    sent = {key: stamp for key, stamp in limit.sent.items()
            if key in keep or 0 <= now - stamp < Alerts.RATE_LIMIT_SECONDS}
    retry = {key: value for key, value in limit.retry.items() if key in keep}
    return replace(limit, sent=frozen(sent), retry=frozen(retry))


def drop(limit, kinds):
    """丟掉這些種類的等待中通知（含失敗後等待重試的）；正在送出的不受影響。"""
    pending = {key: item for key, item in limit.pending.items() if key[0] not in kinds}
    retry = {key: value for key, value in limit.retry.items() if key[0] not in kinds}
    return replace(limit, pending=frozen(pending), retry=frozen(retry))


def clear_pending(limit):
    """偵測到喚醒：等待中的通知都已過時。"""
    return replace(limit, pending=EMPTY, retry=EMPTY)


def offer(limit, alert, now):
    """回傳（新狀態, 現在就可以送出的通知）；送出結果要再交給 delivered() 或 failed()。

    限制期間內、上一則還在送出或等待重試時，改為等待（只保留最新的一則）。"""
    stale = SUPERSEDES.get(alert.kind, ())
    unshown = any(key[0] == PAIRS.get(alert.kind) for key in limit.pending)
    limit = drop(limit, stale) if stale else limit
    if unshown:
        return pruned(limit, now), ()
    if allowed(limit, alert.key, now):
        pending = {key: item for key, item in limit.pending.items() if key != alert.key}
        state = replace(limit, pending=frozen(pending), inflight=limit.inflight | {alert.key})
        return pruned(state, now), (alert,)
    pending = frozen({**limit.pending, alert.key: alert})
    return pruned(replace(limit, pending=pending), now), ()


def delivered(limit, alert, now):
    """送出成功：記下時間，清除失敗次數。"""
    sent = {**limit.sent, alert.key: now}
    retry = {key: value for key, value in limit.retry.items() if key != alert.key}
    return pruned(replace(limit, sent=frozen(sent), retry=frozen(retry),
                          inflight=limit.inflight - {alert.key}), now)


def failed(limit, alert, now):
    """送出失敗：不記時間，30 秒後再試（有更新的等待中通知就改送它）；失敗 5 次就放棄。"""
    attempts = limit.retry.get(alert.key, (0, now))[0] + 1
    inflight = limit.inflight - {alert.key}
    if attempts >= Alerts.SEND_MAX_ATTEMPTS:
        pending = {key: item for key, item in limit.pending.items() if key != alert.key}
        retry = {key: value for key, value in limit.retry.items() if key != alert.key}
        return pruned(replace(limit, pending=frozen(pending), retry=frozen(retry),
                              inflight=inflight), now)
    pending = {alert.key: alert, **limit.pending}
    retry = {**limit.retry, alert.key: (attempts, now + Alerts.SEND_RETRY_SECONDS)}
    return pruned(replace(limit, pending=frozen(pending), retry=frozen(retry),
                          inflight=inflight), now)


def release(limit, now):
    """可以送出的等待中通知：回傳（新狀態, 要送出的通知）；結果同樣交給 delivered()／failed()。"""
    due = tuple(alert for key, alert in limit.pending.items() if allowed(limit, key, now))
    if not due:
        return limit, ()
    keys = {alert.key for alert in due}
    pending = {key: item for key, item in limit.pending.items() if key not in keys}
    return pruned(replace(limit, pending=frozen(pending), inflight=limit.inflight | keys),
                  now), due


def next_release(limit):
    """最早可以送出等待中通知的時間；沒有可排程的等待中通知時回傳 None。"""
    times = [ready_at(limit, key) for key in limit.pending if key not in limit.inflight]
    times = [stamp for stamp in times if stamp is not None]
    return min(times) if times else None
