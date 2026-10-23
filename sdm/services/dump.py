"""--dump：回報筆電問題時可以直接貼上的自我檢查（一個 JSON 物件）。

只列出 power_supply 項目的名稱、類型、scope、狀態，以及「可讀的屬性名稱」（不含讀值）；
另外列出是否有充電門檻、變壓器類型與是否接上、是否有不斷電系統、顯示卡名稱、
UPower 與電源模式服務是否可用、程式版本。
不讀也不列出序號、UUID 或 uevent；名稱中像 MAC 位址的部分改成 xx；不寫任何檔案。
"""
import os
from pathlib import Path
import re
import subprocess

from gi.repository import Gio, GLib

from sdm.constants import Battery, Collect
from sdm.core.fmt import MAC_ADDRESS as MAC, MAC_MASK, clean_text
from sdm.data.gpu import read_amd_gpus
from sdm.data.power_supply import (is_adapter, is_system_battery, read_adapters, read_batteries,
                                   supply_directories)
from sdm.data.sources import command, read
from sdm.data.ups import read_ups
from sdm.data.upower import bus_getter, read_power_profile, read_upower
from sdm.paths import VERSION

SENSITIVE_WORDS = ("serial", "uuid", "uevent")
CHASSIS = re.compile(r"[0-9]{1,3}")
GPU_QUERY = ("--query-gpu=name", "--format=csv,noheader")


def safe_name(name):
    return MAC.sub(MAC_MASK, clean_text(name, Battery.TEXT_MAX_CHARS))


def attribute_names(directory):
    """可讀的屬性檔名稱（不打開檔案）；略過子目錄、連結與含序號的屬性。"""
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    return [entry.name for entry in entries
            if not any(word in entry.name.lower() for word in SENSITIVE_WORDS)
            and not entry.is_symlink() and entry.is_file() and os.access(entry, os.R_OK)]


def supply_entry(directory):
    text = Battery.STATUS_MAX_CHARS
    return dict(name=safe_name(directory.name), type=clean_text(read(directory/"type"), text),
                scope=clean_text(read(directory/"scope"), text),
                status=clean_text(read(directory/"status"), text),
                system_battery=is_system_battery(directory), adapter=is_adapter(directory),
                attributes=attribute_names(directory))


def nvidia_names(run):
    try:
        output = run((Collect.NVIDIA_SMI,) + GPU_QUERY, Collect.NVIDIA_SMI_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    names = [clean_text(line, Battery.TEXT_MAX_CHARS) for line in output.splitlines()]
    return [dict(vendor="nvidia", name=name) for name in names if name]


def gpu_list(root, run):
    amd = [dict(vendor=gpu.vendor, name=gpu.name) for gpu in read_amd_gpus(root)]
    return nvidia_names(run) + amd


def chassis_type(root):
    value = read(Path(root)/"sys/class/dmi/id/chassis_type")
    return value if CHASSIS.fullmatch(value) else None


def system_getter():
    """系統匯流排的屬性讀取函式；連不上時回傳一律讀不到的函式。"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        bus = None
    return bus_getter(bus)


def build_dump(root=Path("/"), get=None, run=command):
    """組出自我檢查的內容；get 為 D-Bus 屬性讀取函式，run 為執行外部命令的函式。"""
    get = system_getter() if get is None else get
    batteries = read_batteries(root)
    return dict(
        app_version=VERSION, mode="laptop" if batteries else "desktop",
        chassis_type=chassis_type(root),
        power_supply=[supply_entry(directory) for directory in supply_directories(root)],
        charge_thresholds=any(battery.thresholds_supported for battery in batteries),
        adapters=[dict(type=adapter.kind, online=adapter.online)
                  for adapter in read_adapters(root)],
        ups_present=read_ups(run) is not None, gpus=gpu_list(root, run),
        upower_available=read_upower(get) is not None,
        power_profiles_available=read_power_profile(get) is not None)
