"""磁碟的唯讀讀取：/proc/diskstats 的累計讀寫量，以及 lsblk 的磁碟與已掛載分割區。

- lsblk 加上 RM、HOTPLUG 判斷外接；型號去掉控制字元與雙向文字控制符。
- 總覽或磁碟頁顯示時每 30 秒執行一次 lsblk，其他時候每 5 分鐘。
所有函式都接受 root，測試時指向假的 /proc 與 /sys。
"""
import json
import os
from pathlib import Path
from typing import NamedTuple

from sdm.constants import Disk
from sdm.core.devices import disk_is_physical
from sdm.core.disk_health import EXTERNAL_LABEL, connection_label, is_external, \
    sanitize_model, valid_block_name

SECTOR_BYTES = 512          # /proc/diskstats 的磁區一律以 512 位元組計
NAME_FIELD = 2
READ_SECTORS_FIELD = 5
WRITE_SECTORS_FIELD = 9
SKIPPED_MOUNT_PREFIX = "/snap/"
LSBLK = "/usr/bin/lsblk"
LSBLK_COLUMNS = "NAME,TYPE,TRAN,SIZE,FSTYPE,MOUNTPOINTS,MODEL,RM,HOTPLUG"
INTERNAL_LABEL = "磁碟"


class DiskCounter(NamedTuple):
    name: str
    read_bytes: int
    write_bytes: int


def is_partition(root, name):
    return (Path(root)/"sys/class/block"/name/"partition").exists()


def parse_diskstats(text, root=Path("/")):
    """整顆實體磁碟（不含分割區、loop、zram 等）開機以來的讀寫位元組。"""
    counters = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) <= WRITE_SECTORS_FIELD:
            continue
        name = fields[NAME_FIELD]
        try:
            read_bytes = int(fields[READ_SECTORS_FIELD]) * SECTOR_BYTES
            write_bytes = int(fields[WRITE_SECTORS_FIELD]) * SECTOR_BYTES
        except ValueError:
            continue
        if disk_is_physical(name) and not is_partition(root, name):
            counters.append(DiskCounter(name, read_bytes, write_bytes))
    return tuple(counters)


def meta_interval(visible):
    """lsblk 的更新間隔：顯示磁碟的頁面開著時 30 秒，其他時候 5 分鐘。"""
    return Disk.META_VISIBLE_INTERVAL if visible else Disk.META_HIDDEN_INTERVAL


def lsblk_command():
    return [LSBLK, "-J", "-b", "-o", LSBLK_COLUMNS]


def volume_usage(mount, fstype):
    """statvfs 的容量、已用與可用位元組；讀不到時回傳 None。"""
    try:
        stat = os.statvfs(mount)
    except OSError:
        return None
    return dict(mount=mount, fstype=fstype or "", total=stat.f_blocks * stat.f_frsize,
                used=(stat.f_blocks - stat.f_bfree) * stat.f_frsize,
                available=stat.f_bavail * stat.f_frsize)


def mounted_volumes(device):
    """磁碟底下所有已掛載的分割區（略過 snap 映像）。"""
    volumes = []
    def visit(node):
        for mount in node.get("mountpoints") or []:
            if mount and not mount.startswith(SKIPPED_MOUNT_PREFIX):
                usage = volume_usage(mount, node.get("fstype"))
                if usage is not None:
                    volumes.append(usage)
        for child in node.get("children", []):
            visit(child)
    visit(device)
    return volumes


def disk_entry(device):
    """lsblk 的一個實體磁碟：型號、連線類型、是否外接、容量與已掛載的分割區用量。"""
    external = is_external(device.get("rm"), device.get("hotplug"), device.get("tran"))
    return dict(name=device["name"],
                model=sanitize_model(device.get("model"),
                                     fallback=EXTERNAL_LABEL if external else INTERNAL_LABEL),
                transport=device.get("tran") or "", size=device.get("size", 0),
                connection=connection_label(device.get("tran")), external=external,
                volumes=mounted_volumes(device))


def parse_lsblk(text):
    """lsblk -J 的輸出 → 實體磁碟清單；格式錯誤時拋出 ValueError 或 KeyError。"""
    root = json.loads(text)
    return [disk_entry(device) for device in root["blockdevices"]
            if device.get("type") == "disk" and valid_block_name(device["name"])
            and disk_is_physical(device["name"])]
