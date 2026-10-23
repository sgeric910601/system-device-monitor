"""桌面通知（libnotify，gir1.2-notify-0.7）：只在主執行緒呼叫。

- 第一次送出時才載入函式庫並 Notify.init("系統與裝置監看")；init 失敗下次再試
  （登入時看板可能比通知服務早啟動）。
- 每則通知都帶 desktop-entry 提示（system-device-monitor）與圖示主題的圖示；
  只有不斷電系統改用電池與電量低是緊急通知。點通知（預設動作）開啟相關頁面。
- 通知物件保留到「closed」為止（否則點通知時找不到回呼）；同一種通知（同一台設備）
  更新原本那則，不會越疊越多，最多保留 32 則。
- 任何失敗（沒有通知服務、init 失敗、GLib.Error、函式庫載入失敗）只記錄：第一次 WARNING、
  之後 DEBUG，絕不拋出。日誌只寫錯誤類型，不寫通知內容。
- probe() 以非同步 D-Bus 呼叫確認是否有通知服務（目前有人提供或可自動啟動），
  結果交給 subscribe() 的監聽者（設定視窗的狀態說明）。
模擬測試與示範模式完全不建立這個物件。
"""
import logging

from gi.repository import Gio, GLib

from sdm.constants import Alerts

LOG = logging.getLogger(__name__)
UNKNOWN, AVAILABLE, UNAVAILABLE = "unknown", "available", "unavailable"
UNAVAILABLE_TEXT = "目前找不到桌面通知服務，提醒不會顯示（需要 GNOME 等有通知功能的桌面）。"
DEFAULT_ACTION = "default"
ACTION_LABEL = "開啟"
DBUS_NAME = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"


def load_notify():
    """載入 libnotify 的 GObject 介面（不初始化）；沒有安裝時拋出 ValueError 或 ImportError。"""
    import gi
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify
    return Notify


def error_name(error):
    code = getattr(error, "code", None)
    return type(error).__name__ + ("" if code is None else f" {code}")


class Notifier:
    def __init__(self, open_page, load=load_notify, connection=lambda: None):
        self.open_page = open_page      # open_page(頁面代號)：點通知時呼叫
        self.load, self.connection = load, connection
        self.module = None
        self.status = UNKNOWN
        self.warned = False
        self.live = {}                  # 通知的鍵 → 還沒關閉的通知物件
        self.listeners = []

    def subscribe(self, listener):
        """listener(status)：通知服務的狀態改變時呼叫。"""
        self.listeners.append(listener)

    def status_text(self):
        return UNAVAILABLE_TEXT if self.status == UNAVAILABLE else ""

    def send(self, alert):
        """送出一則通知；成功回傳 True，任何失敗都只記錄並回傳 False。"""
        try:
            self.prepare(alert).show()
        except Exception as error:      # 最後一道防線：通知失敗不能影響看板
            self.live.pop(alert.key, None)
            self.failed(error)
            return False
        self.set_status(AVAILABLE)
        return True

    def notify_module(self):
        if self.module is None:
            module = self.load()
            if not module.is_initted() and not module.init(Alerts.APP_NAME):
                raise RuntimeError("Notify.init failed")
            self.module = module
        return self.module

    def prepare(self, alert):
        module = self.notify_module()
        notification = self.live.get(alert.key)
        if notification is None:
            notification = module.Notification.new(alert.title, alert.body, alert.icon)
            notification.add_action(DEFAULT_ACTION, ACTION_LABEL, self.opener(alert.page))
            notification.connect("closed", self.closed, alert.key)
            self.remember(alert.key, notification)
        else:
            notification.update(alert.title, alert.body, alert.icon)
        notification.set_urgency(module.Urgency.CRITICAL if alert.critical
                                 else module.Urgency.NORMAL)
        notification.set_hint("desktop-entry", GLib.Variant("s", Alerts.DESKTOP_ENTRY))
        return notification

    def remember(self, key, notification):
        self.live[key] = notification
        while len(self.live) > Alerts.MAX_LIVE:
            self.live.pop(next(iter(self.live)))

    def opener(self, page):
        def activated(*_args):
            try:
                self.open_page(page)
            except Exception as error:      # 回呼來自 D-Bus 訊號，例外不能離開
                LOG.warning("Cannot open the page for a notification: %s", type(error).__name__)
        return activated

    def closed(self, notification, key):
        if self.live.get(key) is notification:
            del self.live[key]

    def failed(self, error):
        level = logging.DEBUG if self.warned else logging.WARNING
        LOG.log(level, "Desktop notification failed: %s", error_name(error))
        self.warned = True
        self.set_status(UNAVAILABLE)

    def set_status(self, status):
        if status == self.status:
            return
        self.status = status
        for listener in tuple(self.listeners):
            listener(status)

    def probe(self):
        """非同步確認通知服務；沒有工作階段匯流排時不做任何事。"""
        connection = self.connection()
        if connection is None:
            return
        connection.call(DBUS_NAME, DBUS_PATH, DBUS_NAME, "NameHasOwner",
                        GLib.Variant("(s)", (Alerts.BUS_NAME,)), GLib.VariantType("(b)"),
                        Gio.DBusCallFlags.NONE, Alerts.PROBE_TIMEOUT_MS, None, self.owner_checked)

    def owner_checked(self, connection, result, *_data):
        try:
            owned = connection.call_finish(result).unpack()[0]
        except GLib.Error as error:
            LOG.debug("Notification service check failed: %s", error_name(error))
            return
        if owned:
            self.set_status(AVAILABLE)
            return
        connection.call(DBUS_NAME, DBUS_PATH, DBUS_NAME, "ListActivatableNames", None,
                        GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                        Alerts.PROBE_TIMEOUT_MS, None, self.activatable_checked)

    def activatable_checked(self, connection, result, *_data):
        try:
            names = connection.call_finish(result).unpack()[0]
        except GLib.Error as error:
            LOG.debug("Notification service check failed: %s", error_name(error))
            return
        self.set_status(AVAILABLE if Alerts.BUS_NAME in names else UNAVAILABLE)
