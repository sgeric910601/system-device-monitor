"""磁碟的唯讀讀取：/proc/diskstats 的累計讀寫量，以及 lsblk 的磁碟與已掛載分割區。

所有函式都接受 root，測試時指向假的 /proc 與 /sys。
"""
import json
import os
from pathlib import Path
from typing import NamedTuple

from sdm.core.devices import disk_is_physical

SECTOR_BYTES = 512          # /proc/diskstats 的磁區一律以 512 位元組計
NAME_FIELD = 2
READ_SECTORS_FIELD = 5
WRITE_SECTORS_FIELD = 9
SKIPPED_MOUNT_PREFIX = "/snap/"


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
        if not disk_is_physical(name) or is_partition(root, name):
            continue
        try:
            counters.append(DiskCounter(name, int(fields[READ_SECTORS_FIELD]) * SECTOR_BYTES,
                                        int(fields[WRITE_SECTORS_FIELD]) * SECTOR_BYTES))
        except ValueError:
            continue
    return tuple(counters)


def volume_usage(mount):
    """statvfs 的容量、已用與可用位元組；讀不到時回傳 None。"""
    try:
        stat = os.statvfs(mount)
    except OSError:
        return None
    return dict(mount=mount, total=stat.f_blocks * stat.f_frsize,
                used=(stat.f_blocks - stat.f_bfree) * stat.f_frsize,
                available=stat.f_bavail * stat.f_frsize)


def mounted_volumes(device):
    """磁碟底下所有已掛載的分割區（略過 snap 映像）。"""
    volumes = []
    def visit(node):
        for mount in node.get("mountpoints") or []:
            if mount and not mount.startswith(SKIPPED_MOUNT_PREFIX):
                usage = volume_usage(mount)
                if usage is not None:
                    volumes.append(usage)
        for child in node.get("children", []):
            visit(child)
    visit(device)
    return volumes


def disk_entry(device):
    """lsblk 的一個實體磁碟：型號、介面、容量與已掛載的分割區用量。"""
    return dict(name=device["name"], model=(device.get("model") or "").strip(),
                transport=device.get("tran") or "", size=device.get("size", 0),
                volumes=mounted_volumes(device))


def parse_lsblk(text):
    """lsblk -J 的輸出 → 實體磁碟清單；格式錯誤時拋出 ValueError 或 KeyError。"""
    root = json.loads(text)
    return [disk_entry(device) for device in root["blockdevices"]
            if device.get("type") == "disk" and disk_is_physical(device["name"])]
