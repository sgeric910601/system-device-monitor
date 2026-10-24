"""充電上限小程式（helper/sdm-charge-limit）測試用的假根目錄、假驅動與假 systemctl。

假根目錄的擁有者是執行測試的使用者（代替 root）；sys/devices 底下放電池的屬性檔，
sys/class/power_supply 底下放指向它們的相對符號連結，與真正的 sysfs 相同。
假驅動取代 os.write：像 sysfs 一樣整段取代檔案內容（不受 O_TRUNC 影響），並可模擬
起點必須小於上限、只接受固定幾個值、拒絕某些值（EINVAL）。
"""
import errno
import os
import unittest

from sdm.constants import ChargeLimit
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import load_helper

helper = MODULE = load_helper("sdm-charge-limit")
END = "charge_control_end_threshold"
START = "charge_control_start_threshold"
DEB_UNIT = "system-device-monitor-charge-limit.service"
LOCAL_UNIT = "system-device-monitor-local-charge-limit.service"
DEB_STATE = "var/lib/system-device-monitor/charge-limit.json"
LOCAL_STATE = "var/lib/system-device-monitor/charge-limit-local.json"
SYSTEMCTL = "/usr/bin/systemctl"
USER = 1000
ENV = {"PKEXEC_UID": str(USER)}
INVOCATION = "0123456789abcdef0123456789abcdef"
UNIT_ENV = {"INVOCATION_ID": INVOCATION}


class Driver:
    """假的驅動：記錄（檔名、值）；ordered 時起點必須小於上限，steps 為只接受的值
    （其他值改成最接近的一個），refuse 中的值以 EINVAL 拒絕。"""

    def __init__(self, ordered=True, steps=None, refuse=()):
        self.ordered, self.steps, self.refuse = ordered, steps, set(refuse)
        self.writes = []

    def __call__(self, fd, data):
        path = os.readlink(f"/proc/self/fd/{fd}")
        name, value = os.path.basename(path), int(data.decode("ascii"))
        self.writes.append((name, value))
        if value in self.refuse or (self.ordered and not self.allowed(path, name, value)):
            raise OSError(errno.EINVAL, "Invalid argument")
        if self.steps:
            value = min(self.steps, key=lambda step: abs(step - value))
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{value}\n".encode("ascii"), 0)
        return len(data)

    @staticmethod
    def allowed(path, name, value):
        other = START if name == END else END
        try:
            with open(os.path.join(os.path.dirname(path), other), encoding="ascii") as handle:
                current = int(handle.read().strip())
        except FileNotFoundError:
            return True
        return value > current if name == END else value < current


class Runner:
    """假的 systemctl：記錄每次呼叫；failing 中的子命令回傳 1，is-enabled 依 enabled。"""

    def __init__(self, failing=(), enabled=False):
        self.calls, self.failing, self.enabled = [], set(failing), enabled

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        if argv[1] == "is-enabled":
            return 0 if self.enabled else 1
        return 1 if argv[1] in self.failing else 0


class Base(unittest.TestCase):
    helper_path = ChargeLimit.DEB_HELPER

    def setUp(self):
        self.root = FakeRoot(self)
        self.runner, self.driver = Runner(), Driver()
        self.output, self.logged, self.sleeps = [], [], []
        self.root.file("usr/lib/systemd/system/" + DEB_UNIT)
        self.root.file("etc/systemd/system/" + LOCAL_UNIT)
        for path in (ChargeLimit.DEB_HELPER, ChargeLimit.LOCAL_HELPER):
            self.root.file(path.lstrip("/"), mode=0o755)
        self.root.directory("var/lib")
        self.root.directory("sys/class/power_supply")

    def battery(self, name="BAT0", end=80, start=75, kind="Battery", scope=None,
                device="platform/PNP0C0A:00"):
        """建立一個 power_supply 項目；值為 None 的屬性不建立。"""
        directory = self.root.directory(f"sys/devices/{device}/power_supply/{name}")
        for attribute, value in (("type", kind), ("scope", scope), (END, end), (START, start)):
            if value is not None:
                (directory/attribute).write_text(f"{value}\n")
        link = self.root.path/"sys/class/power_supply"/name
        link.symlink_to(os.path.relpath(directory, link.parent))
        return directory

    def value(self, name, attribute, device="platform/PNP0C0A:00"):
        path = self.root.path/f"sys/devices/{device}/power_supply/{name}"/attribute
        return int(path.read_text().strip())

    def context(self, euid=0, known=lambda uid: True, helper=None, uid_range=(1000, 60000),
                ppid=4321):
        return MODULE.Context(
            root=str(self.root.path), owner=os.getuid(), run=self.runner, euid=euid,
            user_exists=known, helper_path=helper or self.helper_path, uid_range=uid_range,
            sleep=self.sleeps.append, ppid=ppid, write=self.driver)

    def main(self, argv, env=ENV, **options):
        return helper.main(argv, dict(env), self.context(**options), emit=self.output.append,
                           report=self.logged.append)

    def restore(self, env=UNIT_ENV, **options):
        options.setdefault("ppid", 1)
        return self.main(["restore", "--from-unit"], env=env, **options)

    def systemctl_calls(self):
        return [call[1:] for call in self.runner.calls if call[0] == SYSTEMCTL]
