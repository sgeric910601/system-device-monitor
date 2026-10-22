"""唯讀資料來源轉接；不做任何裝置控制，也不存取 SMTP。"""
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
from sdm.core.devices import battery, normalize_usb, STATES
from sdm.core.jsonfile import load_json
from sdm.core.power_breakdown import SAMPLING
from sdm.data.detail import DetailCollector
from sdm.data.gpu import NvidiaStream, primary_gpu, read_amd_gpus
from sdm.data.logitech_battery import SolaarBattery, apply_battery
from sdm.data.machine import read_model
from sdm.data.network import interface_state, parse_net_dev
from sdm.data.sensors import (cpu_temperature, device_temperatures, hwmon_fans,
                              hwmon_temperatures, storage_temperature)
from sdm.data.power_supply import read_adapters, read_batteries
from sdm.data.sources import command, read
from sdm.data.storage import lsblk_command, meta_interval, parse_diskstats, parse_lsblk
from sdm.data.ups import read_ups
from sdm.data.udisks import SmartSource, bus_caller
from sdm.data.upower import UpowerSource, bus_getter
from sdm.services.power_service import PowerService

GPU_UNAVAILABLE = "GPU 資料暫時無法取得"
ALL_MODULES = frozenset({"cpu", "memory", "disk", "network"})
OVERVIEW_TEMPERATURES = frozenset({"disk", "network"})   # 總覽顯示時才需要 hwmon 溫度的模組

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

class Collectors:
    root = Path("/")     # 讀取 /proc 與 /sys 的根目錄；測試時指向假的目錄
    storage_visible = False     # 總覽或磁碟頁顯示中（由介面設定）：lsblk 改為每 30 秒
    modules = ALL_MODULES       # 開啟中的系統監控模組（由介面設定）；關閉的模組不取樣
    # 總覽頁顯示中（由介面設定）：磁碟讀寫、風扇、其他感測器、磁碟與網路卡溫度只有總覽用得到
    overview_visible = True

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
        self.last_disk_meta = None   # None：還沒列過磁碟，下一次慢速收集立刻執行 lsblk
        self.nvidia = NvidiaStream()  # 由 App 在啟動時 start()、結束時 stop()
        self.power = self.make_power_service()
        self.smart = SmartSource(bus_getter(self.bus), bus_caller(self.bus))
        self.details = DetailCollector(root=self.root, smart=self.smart,
                                       disk_meta=lambda: self.disk_meta, gpus=self.all_gpus)

    def all_gpus(self):
        """溫度分組用的顯示卡讀值：未過期的 NVIDIA 串流與 AMD sysfs。"""
        return tuple(self.nvidia.readings()) + tuple(read_amd_gpus())

    def detail(self, request):
        """頁面顯示時才排程的詳細取樣（見 sdm.core.tiers）。"""
        return self.details.sample(request)

    def system(self):
        """一般層：只取開啟中的模組；磁碟讀寫只有總覽用得到，總覽沒顯示時不讀。"""
        now, modules = time.monotonic(), self.modules
        result = dict(cpu_info=self.cpu_info, stamp=time.time())
        if "cpu" in modules:
            stat = read(self.root/"proc/stat").splitlines()
            result["cpu"] = self.cpu.sample([int(x) for x in stat[0].split()[1:]], now)
            result.update(self.stream_gpu())
        if "memory" in modules:
            used, total = self.memory_used()
            result.update(ram=used / total * 100 if total else None, ram_used=used,
                          ram_total=total)
        if "network" in modules:
            result.update(self.network_summary(now))
        if "disk" in modules and self.overview_visible:
            result["disks"] = self.disk_rates(now)
        return result

    def stream_gpu(self):
        """頂端列的 GPU 使用率直接用 nvidia-smi 串流的最新一行（不另外讀取任何東西）。"""
        stream = getattr(self, "nvidia", None)
        gpu = primary_gpu(stream.readings()) if stream is not None else None
        return {"gpu": gpu} if gpu is not None else {}

    def network_summary(self, now):
        networks = self.network_rates(now)
        physical = [n for n in networks if n["physical"]]
        def net_sum(key):
            known = [n[key] for n in physical if n[key] is not None]
            return sum(known) if known else None
        return dict(networks=networks, rx=net_sum("rx"), tx=net_sum("tx"))

    def memory_used(self):
        """（已用、總量）位元組；已用＝總量減去 MemAvailable。"""
        memory = {}
        for line in read(self.root/"proc/meminfo").splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.split()[0]) * 1024
        total = memory.get("MemTotal", 0)
        return total - memory.get("MemAvailable", memory.get("MemFree", 0)), total

    def network_rates(self, now):
        """連線中的介面（不含 lo）與各自的收送速率；計數器每次都更新，介面恢復連線時才有基準。"""
        networks = []
        for counter in parse_net_dev(read(self.root/"proc/net/dev")):
            state = interface_state(self.root, counter.name)
            rx = self.rates.rate("rx:" + counter.name, counter.rx_bytes, now)
            tx = self.rates.rate("tx:" + counter.name, counter.tx_bytes, now)
            if state.active:
                networks.append(dict(name=counter.name, rx=rx, tx=tx, physical=state.physical,
                                     kind=state.kind))
        return networks

    def disk_rates(self, now):
        return [dict(name=counter.name,
                     read=self.rates.rate("dr:" + counter.name, counter.read_bytes, now),
                     write=self.rates.rate("dw:" + counter.name, counter.write_bytes, now))
                for counter in parse_diskstats(read(self.root/"proc/diskstats"), self.root)]

    def make_power_service(self, model=None):
        """各讀取函式都在呼叫時才查模組層級名稱，測試可替換；UPower 只在筆電模式讀取。"""
        nvidia, system = self.nvidia, UpowerSource(bus_getter(self.bus))
        return PowerService(
            read_ups=lambda: read_ups(command),
            read_gpus=lambda: tuple(nvidia.latest()) + tuple(read_amd_gpus()),
            gpus_pending=nvidia.pending, processor=self.cpu_info.get("name", ""),
            model=read_model() if model is None else model,
            read_batteries=lambda: read_batteries(), read_adapters=lambda: read_adapters(),
            read_upower=system.upower, read_profile=system.profile)

    def power_refresh(self):
        """插拔電源後的重新讀取：只取耗電快照，不重跑溫度與磁碟。"""
        return dict(power=self.power.sample(), stamp=time.time())

    def slow(self):
        """慢速層：耗電一直取樣；CPU 溫度跟著 CPU 模組（頂端列與選單）；
        風扇、其他感測器、磁碟與網路卡溫度只在總覽顯示且模組開啟時讀取。"""
        modules, overview = self.modules, self.overview_visible
        shown = modules if overview else frozenset()
        needs_hwmon = "cpu" in modules or bool(shown & OVERVIEW_TEMPERATURES)
        temperatures = hwmon_temperatures() if needs_hwmon else []
        cpu_temp, cpu_temp_label = (cpu_temperature(temperatures) if "cpu" in modules
                                    else (None, ""))
        power = self.power.sample()
        gpu = primary_gpu(power.gpus)
        pending = power.breakdown.status == SAMPLING
        if "disk" in modules:
            self.refresh_disk_meta()        # 磁碟頁的詳細取樣也要用；沒顯示時每 5 分鐘一次
        storage, used = (self.storage_temperatures(temperatures) if "disk" in shown
                         else ([], set()))
        network_temperatures, network_used = (self.network_temperatures(temperatures)
                                              if "network" in shown else ({}, set()))
        used = used | network_used
        remaining = [s for s in temperatures if id(s) not in used
                     and s["chip"] not in ("k10temp", "coretemp")] if "cpu" in shown else []
        return dict(gpu=gpu, gpus=power.gpus, gpu_error="" if gpu or pending else GPU_UNAVAILABLE,
                    ups=power.ups, power=power, temperatures=remaining, cpu_temp=cpu_temp,
                    cpu_temp_label=cpu_temp_label,
                    fans=hwmon_fans() if "cpu" in shown else [], storage=storage,
                    network_temperatures=network_temperatures, stamp=time.time())

    def storage_temperatures(self, temperatures):
        """每顆磁碟加上依硬體路徑配對的溫度（沒有時用 SMART 快取）；同時回傳已配對感測器的 id。"""
        storage, used = [], set()
        smart = getattr(self, "smart", None)
        for disk in self.disk_meta:
            matched = device_temperatures(temperatures,
                                          self.root/"sys/class/block"/disk["name"]/"device")
            used.update(id(s) for s in matched)
            value = storage_temperature(matched)
            cached = smart.cached(disk["name"]) if value is None and smart is not None else None
            storage.append(dict(disk, temperature=value if cached is None else cached.temperature_c))
        return storage, used

    def network_temperatures(self, temperatures):
        """實體網路介面的 PHY 溫度（取最高）；同時回傳已配對感測器的 id。"""
        found, used = {}, set()
        for interface in (self.root/"sys/class/net").glob("*"):
            if not (interface/"device").exists():
                continue
            matched = device_temperatures(temperatures, interface/"device")
            if matched:
                found[interface.name] = max(s["value"] for s in matched)
                used.update(id(s) for s in matched)
        return found, used

    def refresh_disk_meta(self):
        """只由慢速收集呼叫（一次只有一個在執行）；詳細取樣只讀取 disk_meta，不執行 lsblk。"""
        now = time.monotonic()
        last = self.last_disk_meta
        if last is not None and now - last < meta_interval(self.storage_visible):
            return
        self.last_disk_meta = now
        try:
            self.disk_meta = parse_lsblk(command(lsblk_command(), Collect.LSBLK_TIMEOUT))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            self.disk_meta = []

    def logitech(self):
        return self.logitech_battery.refresh()

    def devices(self):
        logitech = getattr(self, "logitech_battery", None)
        if logitech is not None:
            logitech.observe()
        usb = self.usb_devices()
        bluez, errors = self.bluez_devices()
        # 藍牙無線電屬於連線基礎設施，不列入周邊清單。
        devices, error = self.upower_devices(usb, bluez, logitech)
        errors += [error] if error else []
        devices.extend(bluez.values())
        devices.extend(usb.values())
        devices.sort(key=lambda d: ({"UPS": 0, "無線周邊": 1, "藍牙": 2, "USB": 3}.get(d["kind"], 4), d["name"]))
        return dict(devices=devices, errors=errors, stamp=time.time())

    def usb_devices(self):
        """sysfs 的 USB 終端裝置（不含集線器），以實際路徑為鍵。"""
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
        return usb

    def bluez_devices(self):
        """BlueZ 確認已連線的裝置；服務無法取得時回傳錯誤訊息。"""
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
            return bluez, ["藍牙服務暫時無法取得"]
        return bluez, []

    def upower_devices(self, usb, bluez, logitech):
        """UPower 的周邊與不斷電系統；中途失敗時保留已讀到的部分並回傳錯誤訊息。"""
        devices = []
        try:
            power_paths = dbus(self.bus, "org.freedesktop.UPower", "/org/freedesktop/UPower",
                               "org.freedesktop.UPower", "EnumerateDevices")[0]
            for path in power_paths:
                d = dbus(self.bus, "org.freedesktop.UPower", path,
                         "org.freedesktop.DBus.Properties", "GetAll",
                         GLib.Variant("(s)", ("org.freedesktop.UPower.Device",)))[0]
                item = self.upower_item(path, d, usb, bluez)
                if item is None:
                    continue
                devices.append(item[0])
                if item[1] and logitech is not None and d.get("Type", 0) != 3:
                    apply_battery(devices[-1], logitech.sample(item[1]))
        except GLib.Error:
            return devices, "電池資料服務暫時無法取得"
        return devices, None

    def upower_item(self, path, d, usb, bluez):
        """一個 UPower 裝置 →（設備資料、USB 父裝置路徑）；不列入時回傳 None。"""
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
            return None  # 以 Connected 為準，不採用快取的電量紀錄。
        if not d.get("IsPresent", False) or device_type not in (2, 3, 5, 6):
            return None
        if device_type == 2 and d.get("PowerSupply", False):
            return None  # 筆電本身的電池由電源頁顯示，不是周邊。
        supply = Path("/sys/class/power_supply") / native
        if supply.exists() and read(supply/"online") == "0":
            return None
        parent = usb_parent(native) if native.startswith("/sys/") else (
            usb_parent(supply) if supply.exists() else None)
        if parent:
            usb.pop(parent, None)
        return dict(id="power:" + path, name=d.get("Model") or "電池裝置",
            kind="UPS" if device_type == 3 else "無線周邊", battery=label, percent=value,
            status=STATES.get(d.get("State"), "已偵測"),
            stamp=stamp, source="UPower", detail="USB 電源資料" if device_type == 3 else "接收器回報",
            native=native), parent
