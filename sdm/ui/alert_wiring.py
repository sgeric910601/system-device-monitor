"""看板的通知部分（Monitor 的混入類別）：建立通知、每份耗電快照與設備清單交給 AlertService，
以及「test-notification」動作（--test-notification 從另一個程序觸發）。

- 模擬測試（--smoke-test）與示範模式（--preview）完全不建立通知物件，也不評估提醒；
  「傳送測試通知」與 test-notification 動作只記錄一行。
- 收集結果由 GLib.idle_add 交回主執行緒後才評估，所有通知呼叫都在主執行緒。
- 設定視窗開著（或已建立）時，不斷電系統出現或消失、設備清單與通知服務狀態改變都會通知它。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging

from gi.repository import Gio, GLib

from sdm.constants import Alerts
from sdm.services.alert_service import AlertService
from sdm.services.notifier import Notifier

LOG = logging.getLogger(__name__)
DISABLED_TEXT = "模擬測試與示範模式不會送出通知。"


class AlertMixin:
    notifier = None
    alerts = None
    ups_seen = None         # 上一次通知設定視窗時是否有不斷電系統

    def build_alerts(self):
        """一般執行才建立通知；test-notification 動作一律註冊（沒有通知時只記錄）。"""
        if not (self.smoke or self.preview):
            self.notifier = Notifier(lambda page: self.open_page(None, page),
                                     connection=self.get_dbus_connection)
            self.alerts = AlertService(self.notifier.send, self.schedule, self.remove_source,
                                       defer=GLib.idle_add)
            self.notifier.subscribe(lambda _status: self.notice_changed())
        action = Gio.SimpleAction.new(Alerts.TEST_ACTION, None)
        action.connect("activate", lambda *_: self.send_test_notification())
        self.add_action(action)

    def send_test_notification(self):
        if self.alerts is None:
            LOG.info("Notifications are disabled in smoke tests and preview mode")
            return False
        return self.alerts.send_test()

    def notice_text(self):
        """設定視窗的通知狀態說明；沒有問題時為空字串。"""
        return DISABLED_TEXT if self.notifier is None else self.notifier.status_text()

    def probe_notifications(self):
        if self.notifier is not None:
            self.notifier.probe()

    def ups_present(self):
        power = self.current_power()
        return power is not None and getattr(power, "ups", None) is not None

    def power_alerts(self):
        if self.alerts is not None:
            self.alerts.power(self.current_power(), self.preferences)
        present = self.ups_present()
        if present != self.ups_seen:
            self.ups_seen = present
            if self.settings_window is not None:
                self.settings_window.power_changed()

    def device_alerts(self):
        if self.alerts is not None:
            self.alerts.devices(self.data["devices"], self.preferences)
        if self.settings_window is not None:
            self.settings_window.devices_changed()

    def notice_changed(self):
        if self.settings_window is not None:
            self.settings_window.notice_changed()

    def stop_alerts(self):
        if self.alerts is not None:
            self.alerts.stop()
