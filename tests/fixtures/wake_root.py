"""定時喚醒小程式（helper/sdm-wake）測試用的假根目錄與假的 RTC 驅動。

假根目錄的擁有者是執行測試的使用者（代替 root）：sys/devices/pnp0/00:00/rtc/rtc0 底下放
wakealarm 與 since_epoch，sys/class/rtc/rtc0 是指向它的相對符號連結，與真正的 sysfs 相同。
假驅動取代 os.write：與核心相同，寫 "0" 取消鬧鐘（內容變成空的），已經有鬧鐘時寫入新的時間
回傳 EBUSY；rounding 可以模擬驅動把時間改成別的值，hook 在每次寫入前以寫入的文字呼叫
（可以檢查當時的狀態，或拋出例外模擬中斷）。
"""
import errno
import os
import unittest

from sdm.constants import Wake
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import load_helper

helper = load_helper("sdm-wake")
USER = 1000
ENV = {"PKEXEC_UID": str(USER)}
NOW = 1_800_000_000
RTC_DEVICE = "sys/devices/pnp0/00:00/rtc/rtc0"
RECORD = "run/system-device-monitor/wake"
LOCK = "run/system-device-monitor/wake.lock"
TIMES = "run/system-device-monitor/wake.times"


class Rtc:
    """假的 RTC 驅動：記錄寫入的文字。"""

    def __init__(self, rounding=0, refuse=False):
        self.rounding, self.refuse, self.writes, self.hook = rounding, refuse, [], None

    def __call__(self, fd, data):
        text = data.decode("ascii")
        if self.hook is not None:
            self.hook(text)
        self.writes.append(text)
        if self.refuse:
            raise OSError(errno.EINVAL, "Invalid argument")
        if text != "0" and self.current(fd):
            raise OSError(errno.EBUSY, "Device or resource busy")
        value = "" if text == "0" else f"{int(text) + self.rounding}\n"
        os.ftruncate(fd, 0)
        os.pwrite(fd, value.encode("ascii"), 0)
        return len(data)

    @staticmethod
    def current(fd):
        path = os.readlink(f"/proc/self/fd/{fd}")
        with open(path, encoding="ascii") as handle:
            return handle.read().strip()


class Base(unittest.TestCase):
    helper_path = Wake.DEB_HELPER

    def setUp(self):
        self.root = FakeRoot(self)
        self.rtc = Rtc()
        self.output, self.logged, self.sleeps, self.on_sleep = [], [], [], []
        self.now, self.monotonic = float(NOW), 0.0
        self.boot, self.boot_step = 1000.0, 100.0   # 開機後的秒數；預設每次讀取都過了很久
        for path in (Wake.DEB_HELPER, Wake.LOCAL_HELPER):
            self.root.file(path.lstrip("/"), mode=0o755)
        self.root.directory("run")
        self.make_rtc()

    def make_rtc(self, alarm="", since=None):
        directory = self.root.directory(RTC_DEVICE)
        (directory/"wakealarm").write_text(alarm)
        (directory/"since_epoch").write_text(f"{NOW if since is None else since}\n")
        link = self.root.directory("sys/class/rtc")/"rtc0"
        if not os.path.lexists(link):
            link.symlink_to(os.path.relpath(directory, link.parent))
        return directory

    def alarm(self):
        return (self.root.path/RTC_DEVICE/"wakealarm").read_text().strip()

    def set_alarm(self, value):
        (self.root.path/RTC_DEVICE/"wakealarm").write_text(f"{value}\n" if value else "")

    def record(self, value):
        path = self.root.file(RECORD, f"{value}\n", mode=0o600)
        return path

    def boottime(self):
        self.boot += self.boot_step
        return self.boot

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.monotonic += seconds
        for hook in self.on_sleep:
            hook()

    def context(self, euid=0, known=lambda uid: True, helper_path=None,
                uid_range=(1000, 60000)):
        return helper.Context(
            root=str(self.root.path), owner=os.getuid(), euid=euid, user_exists=known,
            helper_path=helper_path or self.helper_path, uid_range=uid_range,
            sleep=self.sleep, write=self.rtc, clock=lambda: self.monotonic,
            boottime=self.boottime, realtime=lambda: self.now)

    def main(self, argv, env=ENV, **options):
        return helper.main(list(argv), dict(env), self.context(**options),
                           emit=self.output.append, report=self.logged.append)
