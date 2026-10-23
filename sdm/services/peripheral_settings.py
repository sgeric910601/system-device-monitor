"""周邊設備低電量門檻的唯一寫入點（設定 → 周邊設備的下拉選單與連線設備頁的鈴鐺共用）。

- 讀取：依 preferences 的 peripheral_key_salt 算出鍵（見 sdm.core.peripheral_alerts）。
- 寫入：還沒有 salt 時產生一個，並把第一版的鍵換成新鍵（只換得了目前或上一份完整設備清單中的設備）；
  超過 64 項時先丟不在這兩份清單中的設備。所有變更都經由 SettingsStore.update()。
- 被拒絕（設定檔唯讀或值無效）時記錄錯誤類型、回傳 False，並在 problem 寫出原因讓設定視窗與
  鈴鐺的提示顯示；每次被拒絕都通知監聽者（介面要把選項恢復），下一次成功時清除。
- observe()：每份設備清單都交給它，記下目前與上一份完整清單中的設備識別碼（只在記憶體中）。
只在主執行緒呼叫。
"""
import logging

from sdm.core.peripheral_alerts import new_salt, threshold_changes, threshold_for
from sdm.core.settings import PreferencesReadOnlyError

LOG = logging.getLogger(__name__)
READ_ONLY = "無法儲存周邊設備的門檻：設定檔目前無法寫入（可能由較新版本建立）。"
REJECTED = "無法儲存周邊設備的門檻，這次的選擇沒有套用。"


class PeripheralSettings:
    def __init__(self, store, make_salt=new_salt):
        self.store, self.make_salt = store, make_salt
        self.current, self.complete = frozenset(), frozenset()
        self.problem, self.listeners = None, []

    def subscribe(self, listener):
        """listener()：寫入被拒絕，或原因清除時呼叫。"""
        self.listeners.append(listener)

    def observe(self, payload):
        """收集失敗（沒有 stamp）的清單不算；有錯誤訊息的清單不算完整。"""
        if "stamp" not in payload:
            return
        ids = frozenset(str(device["id"]) for device in payload.get("devices", []))
        self.current = ids
        if not payload.get("errors"):
            self.complete = ids

    def known(self):
        return self.current | self.complete

    def threshold(self, device_id):
        prefs = self.store.preferences
        return threshold_for(prefs.peripheral_thresholds, prefs.peripheral_key_salt, device_id)

    def choose(self, device_ids, value):
        """把這些設備的門檻設為 value；成功回傳 True。"""
        try:
            self.store.update(**threshold_changes(self.store.preferences, tuple(device_ids),
                                                  value, self.known(), self.make_salt))
        except (PreferencesReadOnlyError, KeyError, ValueError) as error:
            LOG.warning("Rejected peripheral threshold: %s", type(error).__name__)
            self.report(READ_ONLY if isinstance(error, PreferencesReadOnlyError) else REJECTED)
            return False
        if self.problem is not None:
            self.report(None)
        return True

    def report(self, problem):
        self.problem = problem
        for listener in tuple(self.listeners):
            listener()
