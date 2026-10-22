"""網路介面的唯讀讀取：/proc/net/dev 的累計位元組、/sys/class/net 的介面種類、
/proc/net/route 的主要介面（預設路由），以及主要介面的 IPv4（socket ioctl，不執行外部命令）。

讀檔函式都接受 root 或文字，測試時指向假的 /proc 與 /sys。
"""
import fcntl
from pathlib import Path
import re
import socket
import struct
import time
from typing import NamedTuple

from sdm.constants import Network
from sdm.data.sources import read

KIND_WIRELESS = "Wi-Fi"
KIND_WIRED = "有線網路"
KIND_VIRTUAL = "虛擬介面"
LOOPBACK = "lo"
RX_FIELD = 0                # 冒號之後第 1 個欄位：收到的位元組
TX_FIELD = 8                # 第 9 個欄位：送出的位元組
NET_DEV_HEADER_LINES = 2
DESTINATION_FIELD, FLAGS_FIELD, METRIC_FIELD = 1, 3, 6     # /proc/net/route 的欄位
DEFAULT_DESTINATION = "00000000"
INTERFACE_NAME = re.compile(r"[A-Za-z0-9_.@:-]+")
IPV4_BYTES = 4


class NetCounter(NamedTuple):
    name: str
    rx_bytes: int
    tx_bytes: int


class InterfaceState(NamedTuple):
    physical: bool
    active: bool
    kind: str


def parse_net_dev(text):
    """/proc/net/dev → 各介面（不含 lo）的累計收送位元組；格式不對的列略過。"""
    counters = []
    for line in text.splitlines()[NET_DEV_HEADER_LINES:]:
        name, separator, values = line.partition(":")
        name = name.strip()
        if not separator or not name or name == LOOPBACK:
            continue
        try:
            fields = [int(value) for value in values.split()]
            counters.append(NetCounter(name, fields[RX_FIELD], fields[TX_FIELD]))
        except (ValueError, IndexError):
            continue
    return tuple(counters)


def interface_state(root, name):
    """有 device 的是實體介面；虛擬介面的 operstate 常是 unknown，也算連線中。"""
    directory = Path(root)/"sys/class/net"/name
    operstate = read(directory/"operstate")
    physical = (directory/"device").exists()
    active = operstate == "up" or (not physical and operstate == "unknown")
    if (directory/"wireless").exists():
        kind = KIND_WIRELESS
    else:
        kind = KIND_WIRED if physical else KIND_VIRTUAL
    return InterfaceState(physical, active, kind)


def primary_interface(text):
    """/proc/net/route 中啟用的預設路由（目的地 0.0.0.0）裡 metric 最低的介面。"""
    best = None
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) <= METRIC_FIELD or fields[DESTINATION_FIELD] != DEFAULT_DESTINATION:
            continue
        try:
            flags, metric = int(fields[FLAGS_FIELD], 16), int(fields[METRIC_FIELD])
        except ValueError:
            continue
        if flags & Network.ROUTE_UP and (best is None or metric < best[0]):
            best = (metric, fields[0])
    return None if best is None else best[1]


def valid_interface(name):
    return isinstance(name, str) and 0 < len(name) < Network.IFNAMSIZ and name.isascii() \
        and bool(INTERFACE_NAME.fullmatch(name))


def ipv4_address(name, ioctl=fcntl.ioctl):
    """介面的 IPv4 位址（SIOCGIFADDR）；沒有位址或名稱不合法時回傳 None。"""
    if not valid_interface(name):
        return None
    request = struct.pack(f"{Network.IFREQ_BYTES}s", name.encode())
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            reply = ioctl(sock.fileno(), Network.SIOCGIFADDR, request)
    except OSError:
        return None
    address = reply[Network.ADDRESS_OFFSET:Network.ADDRESS_OFFSET + IPV4_BYTES]
    return socket.inet_ntoa(address) if len(address) == IPV4_BYTES else None


class AddressCache:
    """主要介面的 IPv4：每 30 秒或介面改變時才重新讀取。"""

    def __init__(self, read=ipv4_address, clock=time.monotonic):
        self.read, self.clock = read, clock
        self.name, self.address, self.stamp = None, None, None

    def get(self, name):
        if name is None:
            self.name, self.address, self.stamp = None, None, None
            return None
        now = self.clock()
        if name != self.name or self.stamp is None or now - self.stamp >= Network.IPV4_INTERVAL:
            self.name, self.address, self.stamp = name, self.read(name), now
        return self.address
