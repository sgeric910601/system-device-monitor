"""網路介面的唯讀讀取：/proc/net/dev 的累計位元組與 /sys/class/net 的介面種類。

所有函式都接受 root，測試時指向假的 /proc 與 /sys。
"""
from pathlib import Path
from typing import NamedTuple

from sdm.data.sources import read

KIND_WIRELESS = "Wi-Fi"
KIND_WIRED = "有線網路"
KIND_VIRTUAL = "虛擬介面"
LOOPBACK = "lo"
RX_FIELD = 0                # 冒號之後第 1 個欄位：收到的位元組
TX_FIELD = 8                # 第 9 個欄位：送出的位元組
NET_DEV_HEADER_LINES = 2


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
