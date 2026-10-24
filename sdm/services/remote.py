"""--test-notification：請正在執行的看板送出一則測試通知，這個程序自己不送任何通知、不載入 GTK。

以 Gio.Application 的遠端動作觸發看板的「test-notification」：用同一個應用程式代號與
IS_LAUNCHER 旗標註冊，不會成為主要實例；看板沒有執行時註冊失敗（名稱沒有擁有者）。
看板每 10 秒最多處理一次這個要求，通知能不能顯示取決於桌面的通知服務。
結束碼：0 要求已交給工作階段匯流排；1 看板沒有執行或版本太舊（沒有這個動作）；
2 連不到工作階段匯流排（註冊失敗的原因不是看板沒有執行，或註冊後沒有連線）。
"""
import sys

from gi.repository import Gio, GLib

from sdm.constants import Alerts, App

NOT_RUNNING = "系統與裝置監看沒有在執行：請先啟動看板，再執行 --test-notification。"
TOO_OLD = "正在執行的看板不支援測試通知：請重新啟動看板後再試一次。"
NO_BUS = "連不到桌面工作階段的匯流排：請在登入的桌面中執行。"
NOT_RUNNING_CODES = (Gio.DBusError.SERVICE_UNKNOWN, Gio.DBusError.NAME_HAS_NO_OWNER)


def default_application():
    return Gio.Application(application_id=App.ID, flags=Gio.ApplicationFlags.IS_LAUNCHER)


def not_running(error):
    return any(error.matches(Gio.dbus_error_quark(), code) for code in NOT_RUNNING_CODES)


def request_test_notification(make_application=default_application, stderr=sys.stderr):
    application = make_application()
    try:
        application.register(None)
    except GLib.Error as error:
        missing = not_running(error)
        print(NOT_RUNNING if missing else NO_BUS, file=stderr)
        return 1 if missing else 2
    if not application.get_is_remote():
        print(NOT_RUNNING, file=stderr)
        return 1
    connection = application.get_dbus_connection()
    if connection is None:
        print(NO_BUS, file=stderr)
        return 2
    if not application.has_action(Alerts.TEST_ACTION):
        print(TOO_OLD, file=stderr)
        return 1
    application.activate_action(Alerts.TEST_ACTION, None)
    connection.flush_sync(None)         # 結束前確定要求已送出
    return 0
