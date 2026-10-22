"""hwmon 溫度與風扇的唯讀讀取，以及依 sysfs 硬體路徑配對到磁碟、網路卡（從 collectors 搬來）。

- 溫度、風扇都只讀 /sys/class/hwmon 底下的 *_input、*_label、fan*_max。
- 配對一律依 device 的祖先路徑，絕不依清單順序或猜測的名稱。
- 溫度分組：磁碟用「disk:名稱」、網路卡用「net:介面」當 key，同一顆磁碟的 hwmon 與 SMART
  溫度才會合成一點；NVIDIA 顯示卡沒有 hwmon，改用 nvidia-smi 串流的讀值。
root 可指向假的 /sys 以便測試。
"""
from pathlib import Path

from sdm.core.fmt import number
from sdm.core.thermal import GPU, NETWORK, STORAGE, Fan, ThermalReading, category
from sdm.data.sources import read

HWMON = "sys/class/hwmon"
MILLIDEGREES = 1000
CPU_SENSORS = (("k10temp", "Tctl"), ("k10temp", "Tdie"), ("coretemp", "Package id"))
CPU_MISSING = "CPU 溫度感測器無法取得"
NVIDIA = "nvidia"


def hwmon_directories(root):
    return sorted((Path(root)/HWMON).glob("hwmon*"))


def label_of(path, suffix):
    """標籤檔的內容；沒有標籤檔時用「temp1」「fan2」這類名稱（去掉 _input）。"""
    return read(path.with_name(path.name.replace("_input", suffix)),
                path.name.removesuffix("_input"))


def hwmon_temperatures(root=Path("/")):
    """每個溫度感測器：晶片、標籤、°C 與硬體路徑（device 的實際位置）。"""
    temperatures = []
    for hw in hwmon_directories(root):
        chip = read(hw / "name")
        for path in sorted(hw.glob("temp*_input")):
            value = number(read(path))
            if value is not None:
                temperatures.append(dict(chip=chip, label=label_of(path, "_label"),
                                         value=value / MILLIDEGREES,
                                         device=str((hw/"device").resolve())))
    return temperatures


def read_fans(root=Path("/")):
    """hwmon 風扇的轉速，有 fan*_max 時一併讀取（比例才算得出來）。"""
    fans = []
    for hw in hwmon_directories(root):
        chip = read(hw / "name")
        for path in sorted(hw.glob("fan*_input")):
            value = number(read(path))
            if value is not None:
                top = number(read(path.with_name(path.name.replace("_input", "_max"))))
                fans.append(Fan(chip + " " + label_of(path, "_label"), rpm=value,
                                max_rpm=top if top and top > 0 else None))
    return tuple(fans)


def hwmon_fans(root=Path("/")):
    """總覽頁沿用的格式：名稱與轉速。"""
    return [dict(name=fan.name, value=fan.rpm) for fan in read_fans(root)]


def cpu_temperature(temperatures):
    for chip, prefix in CPU_SENSORS:
        found = [s for s in temperatures if s["chip"] == chip and s["label"].startswith(prefix)]
        if found:
            chosen = max(found, key=lambda s: s["value"])
            return chosen["value"], chosen["label"]
    return None, CPU_MISSING


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


def network_devices(root):
    """實體網路介面名稱 → device 路徑。"""
    return {interface.name: interface/"device"
            for interface in sorted((Path(root)/"sys/class/net").glob("*"))
            if (interface/"device").exists()}


def thermal_readings(temperatures, root, disk_temperatures, gpus):
    """溫度分組用的讀值：hwmon（磁碟與網路卡依路徑配對）、每顆磁碟一點、NVIDIA 顯示卡。"""
    skipped, readings = set(), []
    for name in disk_temperatures:
        skipped.update(id(s) for s in device_temperatures(
            temperatures, Path(root)/"sys/class/block"/name/"device"))
    network = {}
    for name, device in network_devices(root).items():
        for sensor in device_temperatures(temperatures, device):
            network[id(sensor)] = name
    for sensor in temperatures:
        if id(sensor) in skipped:
            continue
        if id(sensor) in network:
            readings.append(ThermalReading("net:" + network[id(sensor)], NETWORK, sensor["value"]))
            continue
        key = f"{sensor['chip']}:{sensor['device']}:{sensor['label']}"
        readings.append(ThermalReading(key, category(sensor["chip"], sensor["label"]),
                                       sensor["value"]))
    readings += [ThermalReading(gpu.gpu_id, GPU, gpu.temp_c) for gpu in gpus
                 if gpu.vendor == NVIDIA]
    readings += [ThermalReading("disk:" + name, STORAGE, value)
                 for name, value in disk_temperatures.items()]
    return tuple(readings)


def gpu_fans(gpus):
    """NVIDIA 顯示卡風扇（百分比）；AMD 顯示卡的風扇已在 hwmon 讀到。"""
    return tuple(Fan(f"{gpu.name} 風扇", percent=gpu.fan_pct) for gpu in gpus
                 if gpu.vendor == NVIDIA and gpu.fan_pct is not None)
