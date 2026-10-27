"""智慧充電的睡眠延遲：系統即將睡眠時，讓排程有時間把充電上限寫好再睡（移植參考專案 SleepGate）。

參考專案用 IOKit 的 IORegisterForSystemPower／IOAllowPowerChange（macOS 才有）；Linux 改用 logind：
- 只在有智慧充電排程時，向 org.freedesktop.login1 要一個 delay 類型的延遲鎖
  （Inhibit("sleep:shutdown", "系統與裝置監看", 說明, "delay")，回傳描述子，關閉即放開）；
  沒有排程時不拿，排程結束立即放開。呼叫一律非同步，不卡住主迴圈。
- PrepareForSleep(true)：呼叫 will_sleep(done)；done() 或 4 秒後（logind 預設最多等 5 秒）放開。
  PrepareForSleep(false)（喚醒）：還有排程就重新拿，再呼叫 did_wake()。
- PrepareForShutdown(true)：呼叫 shutdown(done)（取消精準喚醒的鬧鐘，避免關機後在那個時間
  自動開機），done() 或 4 秒後放開；取消關機（false）時重新拿。
- 拿不到延遲鎖（沒有 logind、polkit 拒絕）時記錄一次警告，60 秒後才再試；仍然會在即將睡眠時
  通知排程（只是不能延後睡眠）。
connection() 回傳系統匯流排連線（沒有時為 None）；測試以假的連線或私人匯流排上的假 logind 代替。
"""
import logging
import os
import time

from gi.repository import Gio, GLib

from sdm.constants import SmartCharging as Limits

LOG = logging.getLogger(__name__)
SERVICE = "org.freedesktop.login1"
PATH = "/org/freedesktop/login1"
MANAGER = "org.freedesktop.login1.Manager"
WHAT, MODE = "sleep:shutdown", "delay"
WHO = "系統與裝置監看"
WHY = "智慧充電：睡眠前調整充電上限、關機前取消定時喚醒"
SIGNALS = ("PrepareForSleep", "PrepareForShutdown")


def glib_schedule(seconds, callback):
    return GLib.timeout_add(int(seconds * 1000), callback)


def glib_cancel(source):
    if GLib.MainContext.default().find_source_by_id(source):
        GLib.source_remove(source)


class SleepGate:
    """只在主執行緒使用。will_sleep(done)、shutdown(done) 做完要呼叫 done()（可以晚一點）。"""

    def __init__(self, connection, will_sleep, did_wake, shutdown=None, schedule=glib_schedule,
                 cancel=glib_cancel, clock=time.monotonic):
        self.connection, self.will_sleep, self.did_wake = connection, will_sleep, did_wake
        self.shutdown = shutdown or (lambda done: done())
        self.schedule, self.cancel, self.clock = schedule, cancel, clock
        self.fd, self.wanted, self.asking, self.retry_at = None, False, False, None
        self.suspending = None          # 已經放開、等系統睡著（或關機）的開始時間：這段時間不再拿
        self.generation, self.timer, self.subscriptions, self.bus = 0, None, [], None
        self.warned = False

    @property
    def holding(self):
        return self.fd is not None

    def start(self):
        self.bus = self.connection()
        if self.bus is None:
            return
        for member in SIGNALS:
            self.subscriptions.append(self.bus.signal_subscribe(
                SERVICE, MANAGER, member, PATH, None, Gio.DBusSignalFlags.NONE, self.signal))

    def stop(self):
        self.wanted = False
        self.release()
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None
        for subscription in self.subscriptions:
            self.bus.signal_unsubscribe(subscription)
        self.subscriptions = []

    # ── 延遲鎖 ─────────────────────────────────────────────

    def set_active(self, wanted):
        """有排程時拿著延遲鎖，沒有時放開。"""
        self.wanted = bool(wanted)
        if not self.wanted:
            self.release()
        elif self.fd is None and not self.asking and not self.waiting_for_sleep():
            self.take()

    def waiting_for_sleep(self):
        """收到即將睡眠後還沒收到喚醒；醒著超過 2 分鐘還沒收到就不再等（單調時鐘睡眠時不走）。"""
        if self.suspending is not None and \
                self.clock() - self.suspending >= Limits.SLEEP_GUARD_SECONDS:
            self.suspending = None
        return self.suspending is not None

    def take(self):
        if self.bus is None or (self.retry_at is not None and self.clock() < self.retry_at):
            return
        self.asking, self.generation = True, self.generation + 1
        self.bus.call_with_unix_fd_list(
            SERVICE, PATH, MANAGER, "Inhibit", GLib.Variant("(ssss)", (WHAT, WHO, WHY, MODE)),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, Limits.INHIBIT_TIMEOUT_MS, None,
            None, self.taken, self.generation)

    def taken(self, bus, result, generation):
        self.asking = False
        try:
            reply, fd_list = bus.call_with_unix_fd_list_finish(result)
            fds = fd_list.steal_fds() if fd_list is not None else []
        except GLib.Error as error:
            self.refused(type(error).__name__)
            return
        index = reply.unpack()[0]
        ours = fds[index] if 0 <= index < len(fds) else None
        for fd in fds:
            if fd != ours:
                os.close(fd)
        if ours is None:
            self.refused("no descriptor")
        elif not self.wanted or generation != self.generation or self.fd is not None:
            os.close(ours)
        else:
            self.fd, self.retry_at = ours, None

    def refused(self, reason):
        self.retry_at = self.clock() + Limits.INHIBIT_RETRY_SECONDS
        LOG.log(logging.DEBUG if self.warned else logging.WARNING,
                "Cannot delay sleep for smart charging (%s)", reason)
        self.warned = True

    def release(self):
        self.generation += 1
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    # ── 睡眠與關機 ─────────────────────────────────────────

    def signal(self, _bus, _sender, _path, _interface, member, parameters):
        starting = bool(parameters.unpack()[0])
        if starting:
            self.suspending = self.clock()
            self.prepare(self.will_sleep if member == SIGNALS[0] else self.shutdown)
            return
        self.suspending = None
        if self.wanted and self.fd is None and not self.asking:
            self.retry_at = None
            self.take()
        if member == SIGNALS[0]:
            self.did_wake()

    def prepare(self, handler):
        """交給排程處理，做完或時間到就放開延遲鎖（每次只放開這一次拿到的鎖）。"""
        state = {"open": True}

        def finish():
            if state["open"]:
                state["open"] = False
                if self.timer is not None:
                    self.cancel(self.timer)
                    self.timer = None
                self.release()
            return False

        if self.timer is not None:
            self.cancel(self.timer)
        self.timer = self.schedule(Limits.SLEEP_RELEASE_SECONDS, finish)
        handler(finish)
