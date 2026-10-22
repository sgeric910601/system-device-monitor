"""各系統監控頁的詳細取樣：只在頁面顯示時由介面排程，只讀被要求的部分。

頂端列需要的資料（CPU、記憶體、網速、溫度、耗電）仍由 Collectors.system()／slow() 取樣；
這裡的追蹤器（各核心、程序群組、主要介面、溫度保留值）與它們分開，互不干擾。
所有路徑都在 root 之下，測試時指向假的 /proc 與 /sys。
"""
from pathlib import Path
import time

from sdm.constants import Thermal
from sdm.core.apps import AppTracker
from sdm.core.counters import Counters
from sdm.core.cpu import CpuTracker, average_mhz, parse_loadavg
from sdm.core.memory import composition, parse_meminfo, parse_pressure, pressure_level
from sdm.core.network import PrimaryRates
from sdm.core.snapshots import (AppsDetail, CpuDetail, DetailSnapshot, DiskStatus,
                                InterfaceStatus, MemoryDetail, NetworkDetail, ThermalDetail,
                                VolumeStatus)
from sdm.core.thermal import ThermalHold, summary
from sdm.data import cgroups
from sdm.data.network import (AddressCache, interface_state, ipv4_address, parse_net_dev,
                              primary_interface)
from sdm.data.sensors import (device_temperatures, gpu_fans, hwmon_temperatures, read_fans,
                              storage_temperature, thermal_readings)
from sdm.data.sources import read
from sdm.data.storage import parse_diskstats

FREQUENCY_GLOB = "cpu[0-9]*/cpufreq/scaling_cur_freq"


class DetailCollector:
    def __init__(self, root=Path("/"), smart=None, disk_meta=lambda: (), gpus=lambda: (),
                 uid=None, clock=time.monotonic, address=ipv4_address):
        self.root, self.smart, self.disk_meta, self.gpus = Path(root), smart, disk_meta, gpus
        self.uid, self.clock = uid, clock
        self.cpu_tracker, self.app_tracker, self.hold = CpuTracker(), AppTracker(), ThermalHold()
        self.rates, self.primary = Counters(), PrimaryRates()
        self.addresses = AddressCache(address, clock)
        self.thermal_cache, self.thermal_at = None, None

    def sample(self, request):
        now = self.clock()
        wants_apps = request.apps_cpu or request.apps_memory
        return DetailSnapshot(
            stamp=now, cpu=self.cpu_detail(now) if request.cpu else None,
            thermal=self.thermal_detail(now) if request.thermal else None,
            apps=self.apps_detail(request, now) if wants_apps else None,
            memory=self.memory_detail() if request.memory else None,
            disks=self.disk_detail(request, now) if request.disks or request.smart else None,
            network=self.network_detail(now) if request.network else None)

    def cpu_detail(self, now):
        directory = self.root/"sys/devices/system/cpu"
        frequencies = (read(path) for path in sorted(directory.glob(FREQUENCY_GLOB)))
        return CpuDetail(self.cpu_tracker.sample(read(self.root/"proc/stat"), now),
                         parse_loadavg(read(self.root/"proc/loadavg")),
                         average_mhz(frequencies))

    def thermal_detail(self, now):
        """溫度與風扇每 5 秒讀一次，中間的取樣沿用上一次的結果。"""
        if self.thermal_cache is not None and now - self.thermal_at < Thermal.INTERVAL:
            return self.thermal_cache
        temperatures = hwmon_temperatures(self.root)
        gpus = tuple(self.gpus())
        readings = thermal_readings(temperatures, self.root,
                                    self.disk_temperatures(temperatures), gpus)
        groups = tuple(summary(self.hold.apply(readings, now)).items())
        self.thermal_cache = ThermalDetail(groups, read_fans(self.root) + gpu_fans(gpus))
        self.thermal_at = now
        return self.thermal_cache

    def disk_temperature(self, temperatures, name, smart=None):
        """依硬體路徑配對的 hwmon 溫度；沒有時用 UDisks2 的 SMART 溫度。"""
        matched = device_temperatures(temperatures, self.root/"sys/class/block"/name/"device")
        value = storage_temperature(matched)
        if value is None:
            smart = smart if smart is not None else self.cached_smart(name)
            value = smart.temperature_c if smart is not None else None
        return value

    def cached_smart(self, name):
        return self.smart.cached(name) if self.smart is not None else None

    def disk_temperatures(self, temperatures):
        found = {}
        for disk in self.disk_meta():
            value = self.disk_temperature(temperatures, disk["name"])
            if value is not None:
                found[disk["name"]] = value
        return found

    def apps_detail(self, request, now):
        readings = cgroups.scan(self.root, self.uid, cpu=request.apps_cpu,
                                memory=request.apps_memory)
        cores = cgroups.cpu_count()
        return AppsDetail(self.app_tracker.sample(readings, now, cores), cores)

    def memory_detail(self):
        values = composition(parse_meminfo(read(self.root/"proc/meminfo")))
        pressure = parse_pressure(read(self.root/"proc/pressure/memory"))
        return MemoryDetail(values, pressure, pressure_level(values, pressure))

    def disk_detail(self, request, now):
        """SMART 依間隔更新；磁碟頁另外回傳每顆磁碟的讀寫、累計、容量與溫度。"""
        meta = tuple(self.disk_meta())
        names = tuple(disk["name"] for disk in meta)
        smart = {}
        if request.smart and self.smart is not None:
            smart = self.smart.refresh(names, attributes=request.smart_attributes)
        if not request.disks:
            return None
        counters = {counter.name: counter for counter in
                    parse_diskstats(read(self.root/"proc/diskstats"), self.root)}
        temperatures = hwmon_temperatures(self.root)
        return tuple(self.disk_status(disk, counters.get(disk["name"]), smart.get(disk["name"]),
                                      temperatures, now) for disk in meta)

    def disk_status(self, disk, counter, smart, temperatures, now):
        name = disk["name"]
        read_rate = write_rate = None
        if counter is not None:
            read_rate = self.rates.rate("dr:" + name, counter.read_bytes, now)
            write_rate = self.rates.rate("dw:" + name, counter.write_bytes, now)
        volumes = tuple(VolumeStatus(v["mount"], v.get("fstype", ""), v["total"], v["used"],
                                     v["available"]) for v in disk["volumes"])
        return DiskStatus(
            name=name, model=disk["model"], connection=disk.get("connection", ""),
            external=disk.get("external", False), size=disk["size"], volumes=volumes,
            read_rate=read_rate, write_rate=write_rate,
            read_total=counter.read_bytes if counter else None,
            write_total=counter.write_bytes if counter else None,
            temperature=self.disk_temperature(temperatures, name, smart), smart=smart)

    def network_detail(self, now):
        primary_name = primary_interface(read(self.root/"proc/net/route"))
        primary, others = None, []
        for counter in parse_net_dev(read(self.root/"proc/net/dev")):
            state = interface_state(self.root, counter.name)
            status = InterfaceStatus(
                counter.name, state.kind, state.physical,
                self.rates.rate("rx:" + counter.name, counter.rx_bytes, now),
                self.rates.rate("tx:" + counter.name, counter.tx_bytes, now),
                counter.rx_bytes, counter.tx_bytes)
            if counter.name == primary_name:
                primary = status
            elif state.active:
                others.append(status)
        name = primary.name if primary else None
        rx, tx, changed = self.primary.sample(name, primary.rx_total if primary else 0,
                                              primary.tx_total if primary else 0, now)
        return NetworkDetail(primary, self.addresses.get(name), rx, tx, changed, tuple(others))
