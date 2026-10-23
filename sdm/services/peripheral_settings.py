"""周邊設備低電量門檻的唯一寫入點（設定 → 周邊設備的下拉選單與連線設備頁的鈴鐺共用）。

- 讀取：依 preferences 的 peripheral_key_salt 算出鍵（見 sdm.core.peripheral_alerts）。
- 寫入：還沒有 salt 時產生一個，並把第一版的鍵換成新鍵（只換得了目前或上一份完整設備清單中的設備）；
  所有變更都經由 SettingsStore.update()。被拒絕（設定檔唯讀或值無效）時只記錄錯誤類型並回傳 False。
- observe()：每份設備清單都交給它，記下目前與上一份完整清單中的設備識別碼（只在記憶體中）。
只在主執行緒呼叫。
"""
import logging

from sdm.core.peripheral_alerts import new_salt, threshold_changes, threshold_for
from sdm.core.settings import PreferencesReadOnlyError

LOG = logging.getLogger(__name__)


class PeripheralSettings:
    def __init__(self, store, make_salt=new_salt):
        self.store, self.make_salt = store, make_salt
        self.current, self.complete = frozenset(), frozenset()

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
            return False
        return True
