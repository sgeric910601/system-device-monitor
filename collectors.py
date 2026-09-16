"""Read-only data adapters. No device-control operations or SMTP access."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import time

import gi
gi.require_version("GUdev", "1.0")
from gi.repository import Gio, GLib, GUdev
from runtime import ALIASES, load_json
from model import (Counters, CpuCounter, battery, normalize_usb, parse_apc,
                   number, STATES, disk_is_physical)

def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return default

def command(args, timeout=3):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C"}, check=True)
    return result.stdout

def dbus(bus, destination, path, interface, method, args=None):
    if bus is None:
        raise GLib.Error("System bus unavailable")
    return bus.call_sync(destination, path, interface, method, args, None,
                         Gio.DBusCallFlags.NONE, 1800, None).unpack()

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
    """Match sysfs ancestry, never list position or a guessed disk name."""
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

class Collectors:
    def __init__(self):
        self.aliases = load_json(ALIASES)
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

    def slow(self):
        temperatures = []
        for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            chip = read(hw / "name")
            for path in sorted(hw.glob("temp*_input")):
                value = number(read(path))
                if value is not None:
                    label = read(path.with_name(path.name.replace("_input", "_label")), path.stem)
                    temperatures.append(dict(chip=chip, label=label, value=value / 1000,
                                             device=str((hw/"device").resolve())))
        cpu_temp, cpu_temp_label = cpu_temperature(temperatures)
        fans = []
        for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            for path in hw.glob("fan*_input"):
                value = number(read(path))
                if value is not None:
                    fans.append(dict(name=read(hw/"name") + " " + read(
                        path.with_name(path.name.replace("_input", "_label")), path.stem),
                        value=value))
        gpu = None
        gpu_error = ""
        try:
            output = command(["/usr/bin/nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,fan.speed,power.draw",
                "--format=csv,noheader,nounits"], 3)
            rows = list(csv.reader(io.StringIO(output)))
            row = rows[0]  # First NVIDIA GPU in driver enumeration order.
            gpu = dict(name=row[0].strip(), usage=number(row[1]), used=number(row[2]),
                       total=number(row[3]), temp=number(row[4]), fan=number(row[5]), watts=number(row[6]))
        except (OSError, subprocess.SubprocessError, IndexError):
            gpu_error = "GPU 資料暫時無法取得"
        ups = None
        try:
            ups = parse_apc(command(["/sbin/apcaccess", "status"], 2))
            ups = {key: ups[key] for key in ("STATUS", "BCHARGE", "TIMELEFT", "LOADPCT") if key in ups}
            if "COMMLOST" in ups.get("STATUS", ""):
                ups = None
        except (OSError, subprocess.SubprocessError):
            pass
        if time.monotonic() - self.last_disk_meta >= 30:
            self.last_disk_meta = time.monotonic()
            try:
                root = json.loads(command(["/usr/bin/lsblk", "-J", "-b",
                    "-o", "NAME,TYPE,TRAN,SIZE,FSTYPE,MOUNTPOINTS,MODEL"], 3))
                disks = []
                for device in root["blockdevices"]:
                    if device.get("type") != "disk" or not disk_is_physical(device["name"]):
                        continue
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
                    disks.append(dict(name=device["name"], model=(device.get("model") or "").strip(),
                                      transport=device.get("tran") or "", size=device.get("size", 0),
                                      volumes=volumes))
                self.disk_meta = disks
            except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                self.disk_meta = []
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
        return dict(gpu=gpu, gpu_error=gpu_error, ups=ups, temperatures=remaining,
                    cpu_temp=cpu_temp, cpu_temp_label=cpu_temp_label, fans=fans,
                    storage=storage, network_temperatures=network_temperatures, stamp=time.time())

    def devices(self):
        devices, errors = [], []
        usb = {}
        for path in sorted(Path("/sys/bus/usb/devices").glob("*")):
            if not (path / "idVendor").exists() or ":" in path.name:
                continue
            if read(path / "bDeviceClass") == "09":  # hubs, not end devices
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
        # Bluetooth radios belong to connection infrastructure, not the peripheral list.
        try:
            paths = dbus(self.bus, "org.freedesktop.UPower", "/org/freedesktop/UPower",
                         "org.freedesktop.UPower", "EnumerateDevices")[0]
            for path in paths:
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
                    continue  # Connected is authoritative, not a cached battery record.
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
        except GLib.Error:
            errors.append("電池資料服務暫時無法取得")
        devices.extend(bluez.values())
        devices.extend(usb.values())
        devices.sort(key=lambda d: ({"UPS": 0, "無線周邊": 1, "藍牙": 2, "USB": 3}.get(d["kind"], 4), d["name"]))
        return dict(devices=devices, errors=errors, stamp=time.time())
