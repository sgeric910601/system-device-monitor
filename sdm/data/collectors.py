"""唯讀資料來源轉接；不做任何裝置控制，也不存取 SMTP。"""
import json
import os
from pathlib import Path
import subprocess
import time

import gi
gi.require_version("GUdev", "1.0")
from gi.repository import Gio, GLib, GUdev
from sdm import paths
from sdm.constants import Collect
from sdm.core.counters import Counters, CpuCounter
from sdm.core.devices import battery, normalize_usb, STATES, disk_is_physical
from sdm.core.fmt import number
from sdm.core.jsonfile import load_json
from sdm.core.power_breakdown import SAMPLING
from sdm.data.gpu import NvidiaStream, primary_gpu, read_amd_gpus
from sdm.data.logitech_battery import SolaarBattery, apply_battery
from sdm.data.machine import read_model
from sdm.data.sources import command, read
from sdm.data.ups import read_ups
from sdm.services.power_service import PowerService

GPU_UNAVAILABLE = "GPU 資料暫時無法取得"

def dbus(bus, destination, path, interface, method, args=None):
    if bus is None:
        raise GLib.Error("System bus unavailable")
    return bus.call_sync(destination, path, interface, method, args, None,
                         Gio.DBusCallFlags.NONE, Collect.DBUS_TIMEOUT_MS, None).unpack()

def usb_parent(path):
    try:
        path = Path(path).resolve()
        for candidate in [path, *path.parents]:
            if (candidate / "idVendor").exists():
                return str(candidate)
    except OSError:
        pass
    return None

def cpu_info():
    records = read("/proc/cpuinfo").split("\n\n")
    names, cores, threads = [], set(), 0
    for record in records:
        data = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
        data = {key.strip(): value.strip() for key, value in data.items()}
        if "processor" not in data:
            continue
        threads += 1
        if data.get("model name"):
            names.append(data["model name"])
        if "core id" in data:
            cores.add((data.get("physical id", "0"), data["core id"]))
    return dict(name=names[0] if names else "CPU", cores=len(cores) or None,
                threads=threads or os.cpu_count())

def cpu_temperature(temperatures):
    for chip, prefix in (("k10temp", "Tctl"), ("k10temp", "Tdie"),
                         ("coretemp", "Package id")):
        found = [s for s in temperatures if s["chip"] == chip and s["label"].startswith(prefix)]
        if found:
            chosen = max(found, key=lambda s: s["value"])
            return chosen["value"], chosen["label"]
    return None, "CPU 溫度感測器無法取得"

def device_temperatures(temperatures, device_path):
    """依 sysfs 祖先路徑配對，絕不依清單順序或猜測的磁碟名稱。"""
    target = Path(device_path).resolve()
    matches = []
    for sensor in temperatures:
        if not sensor.get("device"):
            continue
        source = Path(sensor["device"])
        if source == target or source in target.parents or target in source.parents:
            matches.append(sensor)
    return matches

def storage_temperature(sensors):
    composite = [s for s in sensors if s["label"].lower() == "composite"]
    return max((s["value"] for s in (composite or sensors)), default=None)

def hwmon_temperatures():
    temperatures = []
    for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        chip = read(hw / "name")
        for path in sorted(hw.glob("temp*_input")):
            value = number(read(path))
            if value is not None:
                label = read(path.with_name(path.name.replace("_input", "_label")), path.stem)
                temperatures.append(dict(chip=chip, label=label, value=value / 1000,
                                         device=str((hw/"device").resolve())))
    return temperatures

def hwmon_fans():
    fans = []
    for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        for path in hw.glob("fan*_input"):
            value = number(read(path))
            if value is not None:
                fans.append(dict(name=read(hw/"name") + " " + read(
                    path.with_name(path.name.replace("_input", "_label")), path.stem),
                    value=value))
    return fans

def disk_entry(device):
    """lsblk 的一個實體磁碟：型號、介面、容量與已掛載的分割區用量。"""
    volumes = []
    def visit(node):
        for mount in node.get("mountpoints") or []:
            if mount and not mount.startswith("/snap/"):
                try:
                    s = os.statvfs(mount)
                    capacity = s.f_blocks * s.f_frsize
                    volumes.append(dict(mount=mount, total=capacity,
                        used=(s.f_blocks - s.f_bfree) * s.f_frsize,
                        available=s.f_bavail * s.f_frsize))
                except OSError:
                    pass
        for child in node.get("children", []):
            visit(child)
    visit(device)
    return dict(name=device["name"], model=(device.get("model") or "").strip(),
                transport=device.get("tran") or "", size=device.get("size", 0), volumes=volumes)

class Collectors:
    def __init__(self):
        self.aliases = load_json(paths.ALIASES)
        self.logitech_battery = SolaarBattery()
        self.cpu_info = cpu_info()
        self.cpu = CpuCounter()
        self.rates = Counters()
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            self.bus = None
        self.updates = {}
        self.disk_meta = []
        self.last_disk_meta = 0
        self.nvidia = NvidiaStream()  # 由 App 在啟動時 start()、結束時 stop()
        self.power = self.make_power_service()

    def system(self):
        now = time.monotonic()
        cpu_values = [int(x) for x in read("/proc/stat").splitlines()[0].split()[1:]]
        cpu = self.cpu.sample(cpu_values, now)
        memory = {}
        for line in read("/proc/meminfo").splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.split()[0]) * 1024
        total = memory.get("MemTotal", 0)
        used = total - memory.get("MemAvailable", memory.get("MemFree", 0))
        networks = []
        for line in read("/proc/net/dev").splitlines()[2:]:
            name, values = line.split(":", 1)
            name = name.strip()
            if name == "lo":
                continue
            values = [int(x) for x in values.split()]
            root = Path("/sys/class/net") / name
            operstate = read(root / "operstate")
            physical = (root / "device").exists()
            active = operstate == "up" or (not physical and operstate == "unknown")
            rx = self.rates.rate("rx:" + name, values[0], now)
            tx = self.rates.rate("tx:" + name, values[8], now)
            if active:
                networks.append(dict(name=name, rx=rx, tx=tx, physical=physical,
                                     kind="Wi-Fi" if (root / "wireless").exists() else
                                          ("有線網路" if physical else "虛擬介面")))
        physical = [n for n in networks if n["physical"]]
        def net_sum(key):
            known = [n[key] for n in physical if n[key] is not None]
            return sum(known) if known else None
        disks = []
        for line in read("/proc/diskstats").splitlines():
            fields = line.split()
            name = fields[2]
            root = Path("/sys/class/block") / name
            if (root / "partition").exists() or not disk_is_physical(name):
                continue
            disks.append(dict(name=name, read=self.rates.rate("dr:" + name, int(fields[5]) * 512, now),
                              write=self.rates.rate("dw:" + name, int(fields[9]) * 512, now)))
        return dict(cpu=cpu, cpu_info=self.cpu_info, ram=used / total * 100 if total else None,
                    ram_used=used, ram_total=total, networks=networks,
                    rx=net_sum("rx"), tx=net_sum("tx"), disks=disks, stamp=time.time())

    def make_power_service(self, model=None):
        """不斷電系統與顯示卡讀取都在呼叫時才查模組層級函式，測試可替換。"""
        nvidia = self.nvidia
        return PowerService(
            read_ups=lambda: read_ups(command),
            read_gpus=lambda: tuple(nvidia.latest()) + tuple(read_amd_gpus()),
            gpus_pending=nvidia.pending, processor=self.cpu_info.get("name", ""),
            model=read_model() if model is None else model)

    def slow(self):
        temperatures = hwmon_temperatures()
        cpu_temp, cpu_temp_label = cpu_temperature(temperatures)
        power = self.power.sample()
        gpu = primary_gpu(power.gpus)
        pending = power.breakdown.status == SAMPLING
        self.refresh_disk_meta()
        storage, used = [], set()
        for disk in self.disk_meta:
            matched = device_temperatures(temperatures, f"/sys/class/block/{disk['name']}/device")
            used.update(id(s) for s in matched)
            storage.append(dict(disk, temperature=storage_temperature(matched)))
        network_temperatures = {}
        for interface in Path("/sys/class/net").glob("*"):
            if not (interface/"device").exists():
                continue
            matched = device_temperatures(temperatures, interface/"device")
            if matched:
                network_temperatures[interface.name] = max(s["value"] for s in matched)
                used.update(id(s) for s in matched)
        remaining = [s for s in temperatures if id(s) not in used and s["chip"] not in ("k10temp", "coretemp")]
        return dict(gpu=gpu, gpus=power.gpus, gpu_error="" if gpu or pending else GPU_UNAVAILABLE,
                    ups=power.ups, power=power, temperatures=remaining, cpu_temp=cpu_temp,
                    cpu_temp_label=cpu_temp_label, fans=hwmon_fans(), storage=storage,
                    network_temperatures=network_temperatures, stamp=time.time())

    def refresh_disk_meta(self):
        if time.monotonic() - self.last_disk_meta < Collect.DISK_META_INTERVAL:
            return
        self.last_disk_meta = time.monotonic()
        try:
            root = json.loads(command(["/usr/bin/lsblk", "-J", "-b",
                "-o", "NAME,TYPE,TRAN,SIZE,FSTYPE,MOUNTPOINTS,MODEL"], Collect.LSBLK_TIMEOUT))
            self.disk_meta = [disk_entry(device) for device in root["blockdevices"]
                              if device.get("type") == "disk" and disk_is_physical(device["name"])]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            self.disk_meta = []

    def logitech(self):
        return self.logitech_battery.refresh()

    def devices(self):
        devices, errors = [], []
        logitech = getattr(self, "logitech_battery", None)
        if logitech is not None:
            logitech.observe()
        usb = {}
        for path in sorted(Path("/sys/bus/usb/devices").glob("*")):
            if not (path / "idVendor").exists() or ":" in path.name:
                continue
            if read(path / "bDeviceClass") == "09":  # 集線器不是終端裝置
                continue
            vendor, product = read(path/"idVendor"), read(path/"idProduct")
            raw_name = read(path/"product")
            manufacturer = read(path/"manufacturer")
            name, status, power = normalize_usb(vendor, product, manufacturer, raw_name, getattr(self, "aliases", {}))
            usb[str(path.resolve())] = dict(id="usb:" + str(path.resolve()), name=name,
                kind="USB", status=status, battery=power, percent=None, stamp=0,
                source="USB", detail=f"{vendor}:{product} · {raw_name}",
                vendor=vendor, product=product)
        bluez = {}
        try:
            objects = dbus(self.bus, "org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                           "GetManagedObjects")[0]
            for path, interfaces in objects.items():
                d = interfaces.get("org.bluez.Device1")
                if not d or not d.get("Connected"):
                    continue
                b = interfaces.get("org.bluez.Battery1", {}).get("Percentage")
                name = d.get("Alias") or d.get("Name") or "藍牙裝置"
                key = "bt:" + path
                previous = self.updates.get(key)
                if previous is None or previous[0] != b:
                    self.updates[key] = (b, time.time())
                label, value = battery(1, b, b is not None)
                bluez[path] = dict(id=key, name=name, kind="藍牙", status="已連線",
                    battery=label, percent=value, stamp=self.updates[key][1] if b is not None else 0,
                    source="BlueZ", detail="藍牙電量最後觀測時間", native=path)
        except GLib.Error:
            errors.append("藍牙服務暫時無法取得")
        # 藍牙無線電屬於連線基礎設施，不列入周邊清單。
        try:
            power_paths = dbus(self.bus, "org.freedesktop.UPower", "/org/freedesktop/UPower",
                               "org.freedesktop.UPower", "EnumerateDevices")[0]
            for path in power_paths:
                d = dbus(self.bus, "org.freedesktop.UPower", path,
                         "org.freedesktop.DBus.Properties", "GetAll",
                         GLib.Variant("(s)", ("org.freedesktop.UPower.Device",)))[0]
                native = d.get("NativePath", "")
                device_type = d.get("Type", 0)
                stamp = d.get("UpdateTime", 0)
                label, value = battery(d.get("BatteryLevel", 0), d.get("Percentage"),
                                       bool(stamp) and d.get("IsPresent", True))
                if native.startswith("/org/bluez/"):
                    if native in bluez:
                        item = bluez[native]
                        if item["percent"] is None:
                            item.update(battery=label, percent=value, stamp=stamp, source="UPower")
                    continue  # 以 Connected 為準，不採用快取的電量紀錄。
                if not d.get("IsPresent", False) or device_type not in (2, 3, 5, 6):
                    continue
                supply = Path("/sys/class/power_supply") / native
                if supply.exists() and read(supply/"online") == "0":
                    continue
                parent = usb_parent(native) if native.startswith("/sys/") else (
                    usb_parent(supply) if supply.exists() else None)
                if parent:
                    usb.pop(parent, None)
                devices.append(dict(id="power:" + path, name=d.get("Model") or "電池裝置",
                    kind="UPS" if device_type == 3 else "無線周邊", battery=label, percent=value,
                    status=STATES.get(d.get("State"), "已偵測"),
                    stamp=stamp, source="UPower", detail="USB 電源資料" if device_type == 3 else "接收器回報",
                    native=native))
                if parent and logitech is not None and device_type != 3:
                    apply_battery(devices[-1], logitech.sample(parent))
        except GLib.Error:
            errors.append("電池資料服務暫時無法取得")
        devices.extend(bluez.values())
        devices.extend(usb.values())
        devices.sort(key=lambda d: ({"UPS": 0, "無線周邊": 1, "藍牙": 2, "USB": 3}.get(d["kind"], 4), d["name"]))
        return dict(devices=devices, errors=errors, stamp=time.time())
