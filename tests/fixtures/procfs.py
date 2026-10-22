"""假的 /proc 與 /sys（CPU、記憶體、網路介面、磁碟統計）：兩個取樣點，數字都是虛構的。

step=0 是第一次取樣，step=1 是 2 秒後的第二次取樣：
- CPU：總計忙碌 50／200 刻度（25%），cpu0 忙碌 50%，cpu1 閒置。
- 網路：enp1s0 每秒收 1000、送 500 位元組；wlp2s0 每秒收 2000。
- 磁碟：nvme0n1 每秒讀 512、寫 1024 位元組；sda 沒有活動。
"""
from pathlib import Path

KIB = 1024
GIB_KB = 1024 * 1024       # 1 GiB 以 kB 表示（/proc/meminfo 的單位）

STAT = (
    "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 350 0 0 0 0 0 0\n"
    "cpu1 0 0 0 450 50 0 0 0 0 0\nintr 1 2 3\nctxt 10\n",
    "cpu  140 0 60 940 60 0 0 0 0 0\ncpu0 140 0 60 400 0 0 0 0 0 0\n"
    "cpu1 0 0 0 540 60 0 0 0 0 0\nintr 1 2 3\nctxt 20\n",
)
MEMINFO = {
    "MemTotal": 16 * GIB_KB, "MemFree": 2 * GIB_KB, "MemAvailable": 6 * GIB_KB,
    "Buffers": 256 * KIB, "Cached": 4 * GIB_KB, "SwapCached": 0, "AnonPages": 7 * GIB_KB,
    "Shmem": 512 * KIB, "KernelStack": 32 * KIB, "PageTables": 96 * KIB,
    "SReclaimable": 512 * KIB, "SUnreclaim": 256 * KIB, "SwapTotal": 8 * GIB_KB,
    "SwapFree": 7 * GIB_KB, "Zswap": 128 * KIB, "Zswapped": 384 * KIB,
}
NET_HEADER = ("Inter-|   Receive                                                |  Transmit\n"
              " face |bytes    packets errs drop fifo frame compressed multicast|bytes    "
              "packets errs drop fifo colls carrier compressed\n")
# 介面 →（第一次的收、送位元組，第二次的收、送位元組）
NET_COUNTERS = {"lo": (100, 100, 200, 200), "enp1s0": (5000, 3000, 7000, 4000),
                "wlp2s0": (10000, 2000, 14000, 2000), "veth1": (0, 0, 0, 0),
                "docker0": (50, 50, 50, 50)}
# 介面 →（operstate、有沒有 device、是不是無線網路）
NET_INTERFACES = {"lo": ("unknown", False, False), "enp1s0": ("up", True, False),
                  "wlp2s0": ("up", True, True), "veth1": ("unknown", False, False),
                  "docker0": ("down", False, False)}
# 磁碟 →（第一次讀、寫的磁區數，第二次讀、寫的磁區數，是否為分割區）
DISK_SECTORS = {"nvme0n1": (1000, 2000, 1002, 2004, False),
                "nvme0n1p1": (900, 1900, 902, 1904, True),
                "sda": (50, 60, 50, 60, False), "loop0": (5, 0, 9, 0, False)}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def net_dev(step):
    rows = []
    for name, values in NET_COUNTERS.items():
        rx, tx = values[2 * step], values[2 * step + 1]
        rows.append(f"{name:>6}: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0\n")
    return NET_HEADER + "".join(rows)


def diskstats(step):
    rows = []
    for index, (name, values) in enumerate(DISK_SECTORS.items()):
        read, written = values[2 * step], values[2 * step + 1]
        rows.append(f"   8 {index} {name} 10 0 {read} 5 20 0 {written} 7 0 9 12 0 0 0 0 0 0\n")
    return "".join(rows)


def write_system(root, step=0):
    """寫入 step 那一次取樣的 /proc 檔案；/sys 的介面與磁碟目錄只在第一次建立。"""
    root = Path(root)
    write(root/"proc/stat", STAT[step])
    write(root/"proc/meminfo", "".join(f"{key}: {value} kB\n" for key, value in MEMINFO.items()))
    write(root/"proc/net/dev", net_dev(step))
    write(root/"proc/diskstats", diskstats(step))
    if step:
        return
    for name, (state, device, wireless) in NET_INTERFACES.items():
        directory = root/"sys/class/net"/name
        write(directory/"operstate", state + "\n")
        if device:
            (directory/"device").mkdir()
        if wireless:
            (directory/"wireless").mkdir()
    for name, values in DISK_SECTORS.items():
        directory = root/"sys/class/block"/name
        directory.mkdir(parents=True, exist_ok=True)
        if values[4]:
            write(directory/"partition", "1\n")
