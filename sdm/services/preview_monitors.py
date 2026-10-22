"""示範模式（--preview）的系統監控頁資料：CPU／GPU、記憶體、磁碟與網路，全部是示範值。

不讀這台電腦的 /proc、/sys、程序群組、感測器或磁碟服務；名稱都標明「示範」，
位址只用文件範例網段 192.0.2.0/24。數值與總覽頁一致（同一個時間點算出相同的使用量）。
"""
import math

from sdm.core.apps import SESSION_ID, SYSTEM_ID, AppsSample, AppUsage
from sdm.core.cpu import CpuSample, CpuUsage
from sdm.core.disk_health import NONE, NVME, DiskSmart
from sdm.core.memory import NORMAL, MemoryComposition, Pressure
from sdm.core.monitor_history import MonitorHistory
from sdm.core.snapshots import (AppsDetail, CpuDetail, DetailSnapshot, DiskStatus,
                                InterfaceStatus, MemoryDetail, NetworkDetail, ThermalDetail,
                                VolumeStatus)
from sdm.core.thermal import CPU, GPU, NETWORK, STORAGE, Fan, Stat

GIB = 1024 ** 3
MIB = 1024 ** 2
GB = 1000 ** 3
DEMO_RAM_BYTES = 32 * GIB
DEMO_THREADS = 16
DEMO_RX = 1_200_000.0       # 位元組／秒
DEMO_TX = 240_000.0
DEMO_ADDRESS = "192.0.2.10"
DEMO_GPU_USAGE = 12.0       # 與示範顯示卡的使用率相同
WIRED = "示範有線網路"
VIRTUAL = "示範虛擬網路"
DEMO_DISKS = (
    dict(name="nvme0n1", model="示範固態硬碟", transport="nvme", size=1000 * GB,
         connection="NVMe", external=False,
         volumes=[dict(mount="/", fstype="ext4", total=900 * GB, used=320 * GB,
                       available=580 * GB)]),
    dict(name="sdb", model="示範隨身碟", transport="usb", size=64 * GB, connection="USB",
         external=True,
         volumes=[dict(mount="/media/示範隨身碟", fstype="exfat", total=64 * GB, used=57 * GB,
                       available=7 * GB)]),
)
DEMO_SMART = {
    "nvme0n1": DiskSmart(kind=NVME, updated=1, power_on_hours=1512, temperature_c=41.0,
                         percent_used=2, avail_spare=100, spare_thresh=10,
                         bytes_read=41_300 * GB, bytes_written=28_600 * GB, media_errors=0),
    "sdb": DiskSmart(kind=NONE),
}
DEMO_TOTALS = {"nvme0n1": (85 * GB, 41 * GB), "sdb": (4 * GB, 1_200_000_000)}
# （代號、名稱、圖示、單一核心 CPU %、記憶體）
DEMO_APPS = (
    ("demo.browser", "示範瀏覽器", "web-browser", 34.5, int(1.8 * GIB)),
    ("demo.editor", "示範編輯器", "accessories-text-editor", 12.2, 820 * MIB),
    ("demo.music", "示範音樂", "multimedia-player", 8.1, 410 * MIB),
    ("demo.mail", "示範郵件", "mail-unread", 3.4, 380 * MIB),
    ("demo.terminal", "示範終端機", "utilities-terminal", 1.6, 160 * MIB),
    (SESSION_ID, None, None, 5.0, int(1.2 * GIB)),
    (SYSTEM_ID, None, None, 2.0, 900 * MIB),
)
HISTORY_POINTS = 61
HISTORY_STEP = 2                # 秒；與詳細取樣的間隔相同


def wave(stamp, period):
    return math.sin(stamp / period)


def demo_cpu_percent(now):
    """總覽頁與 CPU／GPU 頁共用的示範 CPU 使用率（%）。"""
    return round(18 + 8 * wave(now, 47), 1)


def demo_ram_used(now):
    return int(DEMO_RAM_BYTES * (0.42 + 0.03 * wave(now, 300)))


def demo_rates(now):
    return DEMO_RX * (1.1 + wave(now, 13)), DEMO_TX * (1.1 + wave(now, 17))


def cpu_detail(now):
    total = demo_cpu_percent(now) / 100
    cores = tuple(CpuUsage(min(1.0, total * (1 + 0.6 * math.sin(index * 1.7))) * 2 / 3,
                           min(1.0, total * (1 + 0.6 * math.sin(index * 1.7))) / 3)
                  for index in range(DEMO_THREADS))
    return CpuDetail(CpuSample(CpuUsage(total * 2 / 3, total / 3), cores), (2.35, 2.10, 1.98),
                     4200.0)


def thermal_detail():
    groups = ((CPU, Stat(52.0, 54.7)), (GPU, Stat(45.0, 45.0)), (STORAGE, Stat(41.0, 41.0)),
              (NETWORK, Stat(47.0, 47.0)))
    return ThermalDetail(groups, (Fan("示範 CPU 風扇", rpm=1150.0, max_rpm=2400.0),
                                  Fan("示範顯示卡 風扇", percent=30.0)))


def apps_detail(request):
    usages = tuple(AppUsage(app_id, cpu, cpu / DEMO_THREADS, memory)
                   for app_id, _, _, cpu, memory in DEMO_APPS)
    labels = tuple((app_id, name, icon) for app_id, name, icon, _, _ in DEMO_APPS if name)
    return AppsDetail(AppsSample(usages, True), DEMO_THREADS, has_cpu=request.apps_cpu,
                      has_memory=request.apps_memory, labels=labels)


def memory_detail(now):
    """組成依總覽頁同一時間的已使用量拆開：可用的四成是空閒、六成是快取。"""
    used = demo_ram_used(now)
    available = DEMO_RAM_BYTES - used
    shared, kernel, other = GIB, 3 * GIB // 2, GIB // 2
    free = int(available * 0.4)
    cache = available - free
    composition = MemoryComposition(
        total=DEMO_RAM_BYTES, app=used - shared - kernel - other, shared=shared, kernel=kernel,
        cache=cache, free=free, available=available, swap_used=512 * MIB, swap_total=8 * GIB,
        zswap=256 * MIB, zswapped=768 * MIB)
    return MemoryDetail(composition, Pressure(0.4, 0.0), NORMAL)


def disk_detail(now):
    rx, tx = demo_rates(now)
    disks = []
    for disk in DEMO_DISKS:
        internal = not disk["external"]
        volumes = tuple(VolumeStatus(v["mount"], v["fstype"], v["total"], v["used"],
                                     v["available"]) for v in disk["volumes"])
        read_total, write_total = DEMO_TOTALS[disk["name"]]
        disks.append(DiskStatus(
            name=disk["name"], model=disk["model"], connection=disk["connection"],
            external=disk["external"], size=disk["size"], volumes=volumes,
            read_rate=rx / 3 if internal else 0.0, write_rate=tx / 2 if internal else 0.0,
            read_total=read_total, write_total=write_total,
            temperature=41.0 if internal else None, smart=DEMO_SMART[disk["name"]]))
    return tuple(disks)


def network_detail(now):
    rx, tx = demo_rates(now)
    primary = InterfaceStatus(WIRED, "有線網路", True, rx, tx, 12_400_000_000, 1_900_000_000)
    other = InterfaceStatus(VIRTUAL, "虛擬介面", False, 0.0, 0.0, 52_000_000, 48_000_000)
    return NetworkDetail(primary, DEMO_ADDRESS, rx, tx, False, (other,))


def demo_detail(request, now, stamp):
    """now 為 Unix 時間（與總覽頁一致），stamp 為單調時鐘秒數（曲線用）。"""
    return DetailSnapshot(
        stamp=stamp, cpu=cpu_detail(now) if request.cpu else None,
        thermal=thermal_detail() if request.thermal else None,
        apps=apps_detail(request) if request.apps_cpu or request.apps_memory else None,
        memory=memory_detail(now) if request.memory else None,
        disks=disk_detail(now) if request.disks else None,
        network=network_detail(now) if request.network else None)


def history(stamp, now, values):
    """最後一點在 stamp 前 2 秒；values(wall) 依當時的 Unix 時間算出（第一條、第二條線）。"""
    result = MonitorHistory()
    for index in range(HISTORY_POINTS):
        offset = (HISTORY_POINTS - index) * HISTORY_STEP
        a, b = values(now - offset)
        result = result.appended(a, b, t=stamp - offset)
    return result


def demo_histories(stamp, now):
    """各頁預先填好的 2 分鐘示範曲線：公式與即時示範值相同，接得上之後的資料。"""
    def disk(wall):
        rx, tx = demo_rates(wall)
        return rx / 3, tx / 2
    return {
        "cpu": history(stamp, now, lambda wall: (demo_cpu_percent(wall), DEMO_GPU_USAGE)),
        "memory": history(stamp, now, lambda wall: (demo_ram_used(wall) / DEMO_RAM_BYTES * 100,
                                                    None)),
        "disk:nvme0n1": history(stamp, now, disk),
        "disk:sdb": history(stamp, now, lambda wall: (0.0, 0.0)),
        "network": history(stamp, now, demo_rates),
    }
