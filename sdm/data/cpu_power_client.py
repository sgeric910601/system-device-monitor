"""CPU 功耗取樣服務（helper/sdm-cpu-power-service）的客戶端。

- 只在設定開啟、元件已安裝而且「電源」頁顯示時啟用（set_active）；停用後下一次 request()
  關閉連線，之後完全不通訊，服務閒置 30 秒後自行結束。
- 連線前確認 socket 是屬於自己的 Unix socket（drop-in 的 SocketUser）；不是就不連。
- request() 在收集執行緒中呼叫（不會卡住介面）：送一行「r」，讀回一行 JSON
  （最多 256 位元組、逾時 1 秒）。連線在兩次請求之間保留，服務端才有每個連線自己的基準。
- 失敗（拒絕連線、逾時、格式錯誤、服務回報 error）時關閉連線，至少等 1、2、4…60 秒再試；
  成功後重設。latest() 回傳最後一次的讀值（不可變），任何執行緒都可以讀。
"""
from dataclasses import dataclass
import json
import logging
import math
import os
import socket
import stat
import threading
import time
from typing import Optional

from sdm.constants import CpuPower, Power

LOG = logging.getLogger(__name__)
SOCKET_TIMEOUT = CpuPower.SOCKET_TIMEOUT
MAX_REPLY_BYTES = CpuPower.MAX_REPLY_BYTES
BACKOFF_INITIAL = CpuPower.BACKOFF_INITIAL
BACKOFF_MAX = CpuPower.BACKOFF_MAX
FRESH_SECONDS = 3.0                 # 參考專案 freshWatts 的預設最長時間
STATUSES = ("ok", "sampling", "error")


@dataclass(frozen=True)
class CpuPowerReading:
    package_w: float                # 處理器插槽（package）功耗
    core_w: Optional[float]         # 其中核心（core）的部分；讀不到時為 None
    stamp: float                    # 收到回應時的單調時鐘秒數


def valid_watts(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    return 0 <= value < Power.COMPONENT_MAX_W


def fresh_watts(reading, now, max_age=FRESH_SECONDS):
    """reading 在 max_age 秒內而且數值合理時回傳 package 瓦數，否則 None。"""
    if reading is None or not valid_watts(reading.package_w):
        return None
    return reading.package_w if 0 <= now - reading.stamp <= max_age else None


def parse_reply(line):
    """（狀態、package W、core W）；格式不符時為 None。"""
    try:
        value = json.loads(line.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or type(value.get("v")) is not int \
            or value["v"] != CpuPower.PROTOCOL or value.get("status") not in STATUSES:
        return None
    status = value["status"]
    if status != "ok":
        return status, None, None
    package, core = value.get("package_w"), value.get("core_w")
    if not valid_watts(package) or (core is not None and not valid_watts(core)):
        return None
    return status, float(package), None if core is None else float(core)


def connect_unix(path, timeout):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except BaseException:
        sock.close()
        raise
    return sock


def read_line(sock):
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(MAX_REPLY_BYTES + 1)
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
        if len(data) > MAX_REPLY_BYTES:
            raise ValueError("reply too long")
    line, _, rest = data.partition(b"\n")
    if rest:
        raise ValueError("unexpected data")
    return line


class CpuPowerClient:
    def __init__(self, clock=time.monotonic, connect=connect_unix, lstat=os.lstat,
                 uid=os.getuid):
        self.clock, self.connect, self.lstat, self.uid = clock, connect, lstat, uid
        self.path, self.active, self.reset = None, False, False
        self.socket, self.reading = None, None
        self.retry_at, self.backoff = 0.0, BACKOFF_INITIAL
        self.lock = threading.Lock()

    def configure(self, path):
        """socket 的位置（None：元件沒有安裝）；改變時下一次 request() 重新連線。"""
        if path != self.path:
            self.path, self.reset, self.reading = path, True, None

    def set_active(self, active):
        """在主執行緒呼叫；實際的關閉在下一次 request()（收集執行緒）進行。"""
        if active == self.active:
            return
        self.active = active
        self.retry_at, self.backoff = 0.0, BACKOFF_INITIAL
        if not active:
            self.reading = None

    def latest(self):
        return self.reading

    def request(self):
        with self.lock:
            if self.reset or not self.active or self.path is None:
                self.reset = False
                self.close()
            if not self.active or self.path is None or self.clock() < self.retry_at:
                return
            try:
                self.exchange()
            except (OSError, ValueError) as error:
                LOG.debug("CPU power request failed: %s", type(error).__name__)
                self.fail()

    def exchange(self):
        if self.socket is None:
            self.socket = self.open()
        self.socket.sendall(CpuPower.REQUEST)
        reply = parse_reply(read_line(self.socket))
        if reply is None or reply[0] == "error":
            raise ValueError("unusable reply")
        status, package, core = reply
        if status == "ok":
            self.reading = CpuPowerReading(package, core, self.clock())
        self.backoff = BACKOFF_INITIAL

    def open(self):
        info = self.lstat(self.path)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.uid():
            raise PermissionError("socket is not ours")
        return self.connect(self.path, SOCKET_TIMEOUT)

    def fail(self):
        self.close()
        self.reading = None
        self.retry_at = self.clock() + self.backoff
        self.backoff = min(self.backoff * 2, BACKOFF_MAX)

    def close(self):
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None
