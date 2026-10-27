"""一鍵更新的 pkexec 執行器（Gio.Subprocess，非同步，只在主執行緒呼叫）。

pkexec 授權後由 root 執行 sdm-update，一般使用者無法結束它；介面若提早放棄等待，就會在 root 還在安裝時
又讓使用者按一次「安裝更新」。所以這裡從不放棄、也從不結束程序：一直等到程序真的結束才呼叫一次
done(結束碼, 標準輸出)（無法啟動或取不到結束碼時為 None）。超過 slow_after 秒還沒結束時呼叫一次
on_slow()，設定頁改顯示「仍在安裝中…」。
"""
import logging

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)


class PatientRunner:
    def __init__(self, timeout_add=GLib.timeout_add_seconds, source_remove=GLib.source_remove):
        self.timeout_add, self.source_remove = timeout_add, source_remove

    def run(self, argv, slow_after, on_slow, done):
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        try:
            process = Gio.Subprocess.new(argv, flags)
        except GLib.Error as error:
            LOG.warning("Cannot start the update helper: %s", error.domain)
            done(None, "")
            return
        call = {"timer": 0, "done": done}
        call["timer"] = self.timeout_add(slow_after, self.slow, call, on_slow)
        process.communicate_async(None, None, self.communicated, call)

    def slow(self, call, on_slow):
        call["timer"] = 0
        on_slow()
        return False

    def communicated(self, process, result, call):
        try:
            _, output, _ = process.communicate_finish(result)
        except GLib.Error:
            process.wait_async(None, self.exited, call)     # 仍然等到程序真的結束
            return
        text = output.get_data().decode("utf-8", "replace") if output is not None else ""
        self.finish(process, call, text)

    def exited(self, process, result, call):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        self.finish(process, call, "")

    def finish(self, process, call, text):
        if call["timer"]:
            self.source_remove(call["timer"])
            call["timer"] = 0
        done, call["done"] = call["done"], None
        status = process.get_exit_status() if process.get_if_exited() else None
        if done is not None:
            done(status, text)
