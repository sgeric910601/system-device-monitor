"""示範模式（--preview）的智慧充電排程：只存在記憶體，不寫入任何檔案、不執行任何程式。

筆電示範一律是「9 小時後充到 100%、等待時維持 80%、精準喚醒」（時間取整到 5 分鐘），
狀態依示範情境的電量與接電即時判斷；桌機示範沒有排程。
"""
from sdm.core.charging_plan import PRECISE_WAKE, Once, make_plan
from sdm.services.preview import LAPTOPS

DEMO_HOURS = 9
ROUND_SECONDS = 5 * 60
DEMO_TARGET, DEMO_HOLD = 100, 80
DEMO_ID = "0" * 32


def demo_plan(scenario, now, limit):
    """示範情境的排程；桌機為 None。limit 是示範的充電上限（當成原本的上限）。"""
    if scenario not in LAPTOPS:
        return None
    deadline = (int(now) // ROUND_SECONDS + DEMO_HOURS * 3600 // ROUND_SECONDS) * ROUND_SECONDS
    return make_plan(Once(float(deadline)), float(deadline), DEMO_TARGET, DEMO_HOLD, limit,
                     PRECISE_WAKE, now, DEMO_ID)
