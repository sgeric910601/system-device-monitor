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


LOADAVG = "0.75 1.53 1.86 1/700 7776\n"
PRESSURE = ("some avg10=12.00 avg60=1.00 avg300=0.50 total=100\n"
            "full avg10=0.50 avg60=0.10 avg300=0.00 total=50\n")
# 預設路由：enp1s0（metric 100）優先於 wlp2s0；閘道為文件範例位址 192.0.2.1
ROUTE = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
         "enp1s0\t00000000\t010200C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
         "wlp2s0\t00000000\t010200C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n")
FREQUENCIES_KHZ = (4_200_000, 3_800_000)


def write_system(root, step=0):
    """寫入 step 那一次取樣的 /proc 檔案；/sys 的介面與磁碟目錄只在第一次建立。"""
    root = Path(root)
    write(root/"proc/stat", STAT[step])
    write(root/"proc/loadavg", LOADAVG)
    write(root/"proc/pressure/memory", PRESSURE)
    write(root/"proc/net/route", ROUTE)
    for index, khz in enumerate(FREQUENCIES_KHZ):
        write(root/f"sys/devices/system/cpu/cpu{index}/cpufreq/scaling_cur_freq", f"{khz}\n")
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


# hwmon 目錄 →（晶片名稱、device 指向的硬體目錄、溫度 {檔名: (標籤, 毫度)}、風扇 {編號: (標籤, 轉速, 最高)}）
HWMON = (
    ("k10temp", "pci0/cpu", {"temp1": ("Tctl", 47125), "temp3": ("Tccd1", 41125)}, {}),
    ("nvme", "pci0/nvme/nvme0", {"temp1": ("Composite", 40850)}, {}),
    ("amdgpu", "pci0/gpu", {"temp1": ("edge", 41000)}, {1: (None, 1200, 3000)}),
    ("r8169_0_100", "pci0/nic/mdio", {"temp1": (None, 56500)}, {}),
    ("nct6775", "platform/nct", {}, {1: ("CPU Fan", 900, None)}),
    ("acpitz", "virtual/thermal/tz0", {"temp1": (None, 30000)}, {}),
)


def link(path, target):
    """建立（或取代）指向 target 的符號連結。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir() and not path.is_symlink():
        path.rmdir()
    elif path.is_symlink():
        path.unlink()
    path.symlink_to(target)


def write_hwmon(root):
    """假的 /sys/class/hwmon，並讓 nvme0n1 與 enp1s0 的 device 指向對應的硬體目錄。"""
    root = Path(root)
    devices = root/"sys/devices"
    for index, (chip, device, temperatures, fans) in enumerate(HWMON):
        hwmon = root/"sys/class/hwmon"/f"hwmon{index}"
        write(hwmon/"name", chip + "\n")
        (devices/device).mkdir(parents=True, exist_ok=True)
        link(hwmon/"device", devices/device)
        for stem, (label, value) in temperatures.items():
            write(hwmon/f"{stem}_input", f"{value}\n")
            if label:
                write(hwmon/f"{stem}_label", label + "\n")
        for number, (label, rpm, top) in fans.items():
            write(hwmon/f"fan{number}_input", f"{rpm}\n")
            if label:
                write(hwmon/f"fan{number}_label", label + "\n")
            if top:
                write(hwmon/f"fan{number}_max", f"{top}\n")
    link(root/"sys/class/block/nvme0n1/device", devices/"pci0/nvme/nvme0")
    link(root/"sys/class/net/enp1s0/device", devices/"pci0/nic")


# 程序群組（使用者 1000）：單元 →（第一次、第二次的累計 CPU 微秒，anon 位元組）
CGROUP_UNITS = {
    "app.slice/app-gnome-example\\x2dbrowser-225.scope": (0, 1_000_000, 300 * 1024 ** 2),
    "app.slice/app-com.example.Editor-77.scope": (0, 200_000, 100 * 1024 ** 2),
    "session.slice": (0, 100_000, 50 * 1024 ** 2),
}


def write_cgroups(root, step=0, uid=1000):
    base = Path(root)/f"sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service"
    for unit, values in CGROUP_UNITS.items():
        write(base/unit/"cpu.stat", f"usage_usec {values[step]}\nuser_usec 0\n")
        write(base/unit/"memory.stat", f"anon {values[2]}\nfile 0\n")
