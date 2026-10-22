"""唯讀讀取程序群組（cgroup v2）的 CPU 時間與記憶體，供各 App 排行使用。

- 使用者的 App：user@<uid>.service 底下 app.slice 與 background.slice 的每個單元。
- 「GNOME 桌面」：session.slice（GNOME Shell 等）加上登入工作階段 session-*.scope（含 Xorg）。
- 「系統服務」：/sys/fs/cgroup/system.slice 整體。
只讀 cpu.stat 的 usage_usec 與 memory.stat 的 anon（沒有時用 memory.current），
不跟隨符號連結，不讀任何程序的命令列。root 可指向假的目錄以便測試。
"""
import os
from pathlib import Path
import re

from sdm.core.apps import SESSION_ID, SYSTEM_ID, CgroupReading, parse_unit
from sdm.data.sources import read

CGROUP = "sys/fs/cgroup"
USER_SLICES = ("app.slice", "background.slice")
SESSION_SCOPE = re.compile(r"session-[A-Za-z0-9]+\.scope")


def user_paths(root, uid):
    user = Path(root)/CGROUP/"user.slice"/f"user-{uid}.slice"
    return user, user/f"user@{uid}.service"


def field(text, name):
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key == name:
            try:
                return int(value)
            except ValueError:
                return None
    return None


def usage_usec(text):
    return field(text, "usage_usec")


def anon_bytes(text):
    return field(text, "anon")


def memory_bytes(directory):
    value = anon_bytes(read(directory/"memory.stat"))
    if value is None:
        text = read(directory/"memory.current")
        value = int(text) if text.isdigit() else None
    return value


def reading(directory, key, app_id, cpu, memory):
    return CgroupReading(key, app_id,
                         usage_usec(read(directory/"cpu.stat")) if cpu else None,
                         memory_bytes(directory) if memory else None)


def subdirectories(directory):
    """直接子目錄（不跟隨符號連結），依名稱排序；目錄不存在時為空。"""
    try:
        with os.scandir(directory) as entries:
            names = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted(names)


def app_readings(service, cpu, memory):
    found = []
    for slice_name in USER_SLICES:
        for name in subdirectories(service/slice_name):
            app_id = parse_unit(name)
            if app_id is not None:
                found.append(reading(service/slice_name/name, slice_name + "/" + name, app_id,
                                     cpu, memory))
    return found


def desktop_readings(user, service, cpu, memory):
    found = []
    if (service/"session.slice").is_dir():
        found.append(reading(service/"session.slice", "session.slice", SESSION_ID, cpu, memory))
    for name in subdirectories(user):
        if SESSION_SCOPE.fullmatch(name):
            found.append(reading(user/name, name, SESSION_ID, cpu, memory))
    return found


def scan(root=Path("/"), uid=None, cpu=True, memory=True):
    """所有 App、GNOME 桌面與系統服務的讀值；沒有 cgroup v2 使用者階層時回傳空的 tuple。"""
    uid = os.getuid() if uid is None else uid
    user, service = user_paths(root, uid)
    if not service.is_dir():
        return ()
    found = app_readings(service, cpu, memory) + desktop_readings(user, service, cpu, memory)
    system = Path(root)/CGROUP/"system.slice"
    if system.is_dir():
        found.append(reading(system, "system.slice", SYSTEM_ID, cpu, memory))
    return tuple(found)


def cpu_count():
    return os.cpu_count() or 1
