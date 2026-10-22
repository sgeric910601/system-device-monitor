"""示範模式（--preview）的資料：假的讀取函式、合成的 7 天歷史與示範設備。

用途是截圖與檢查版面，所以：
- 只存在記憶體，不讀寫任何使用者檔案（設定、歷史、裝置別名、自動啟動）。
- 不啟動 nvidia-smi、lsblk、Solaar 等外部程序，也不連系統匯流排。
- 不讀這台電腦的處理器、溫度、風扇、網路介面或磁碟；名稱都標明「示範」，
  截圖不含任何這台電腦的資料。
"""
from dataclasses import replace
from datetime import datetime
import math
import time

from sdm.constants import History, Preview
from sdm.core.battery import combine
from sdm.core.history import HistorySample
from sdm.core.settings import LEGACY_DEFAULTS, Preferences, Settings
from sdm.data.collectors import GPU_UNAVAILABLE, Collectors
from sdm.data.gpu import GpuReading, primary_gpu
from sdm.data.power_supply import AdapterReading, BatteryReading
from sdm.data.ups import UpsReading
from sdm.data.upower import UpowerReading
from sdm.services.power_service import PowerService
from sdm.services.preview_monitors import (DEMO_DISKS, DEMO_GPU_USAGE, DEMO_RAM_BYTES,
                                           DEMO_THREADS, VIRTUAL, WIRED, demo_cpu_percent,
                                           demo_detail, demo_histories, demo_ram_used, demo_rates)

SCENARIOS = Preview.SCENARIOS
DESKTOP = "desktop"
STEP_SECONDS = 600                       # 合成歷史每 10 分鐘一筆
SECONDS_PER_MINUTE = 60
HOLD_PERCENT = 80                        # 示範的充電上限
CHARGE_PER_STEP = 3.3                    # 每 10 分鐘約 +3.3%（每小時約 20%）
DRAIN_PER_STEP = 1.4
LOWEST_PERCENT = 15
FIRST_PERCENT = 70
BEFORE_FINAL_SECONDS = 3600              # 最後一段之前一小時的接電狀態與最後一段相反
PLUGGED_HOURS = ((0, 8), (12, 13), (18, 24))   # 每天接電的時段（本機時間）
BUSY_HOURS = (9, 18)                     # 桌機示範：上班時段耗電較高
DEMO_PROCESSOR = "示範處理器"
DEMO_MODEL = "示範機型"
DEMO_UPTIME_SECONDS = (3 * 24 + 4) * 3600   # 示範的開機時間：3 天 4 小時
GIB = 1024 ** 3

THRESHOLDS = dict(start_threshold=75, end_threshold=HOLD_PERCENT, behaviour="auto")
BATTERY_BASE = BatteryReading(
    name="BAT0", capacity_level="Normal", energy_full_wh=58.0, energy_design_wh=63.0,
    voltage_v=12.45, voltage_min_design_v=11.55, cycle_count=212, temperature_c=31.4,
    technology="Li-poly", manufacturer="示範電池", model="Demo 58Wh", **THRESHOLDS)
MAINS_OFF = AdapterReading(name="AC", kind="Mains", online=False)
MAINS_ON = AdapterReading(name="AC", kind="Mains", online=True)
USB_PD = AdapterReading(name="ucsi-source-psy-USBC000:001", kind="USB", online=True,
                        voltage_v=20.0, current_a=1.25, rated_w=65.0)

# 情境 →（電池、變壓器、UPower、電源模式、最後一段的分鐘數、最後一段是否接電、目前電量）
LAPTOPS = {
    "laptop-battery": (
        replace(BATTERY_BASE, status="Discharging", capacity=78, energy_now_wh=45.2,
                power_w=6.5),
        (MAINS_OFF,),
        UpowerReading(on_battery=True, is_battery=True, state=2, percentage=78.0,
                      time_to_empty_s=445 * SECONDS_PER_MINUTE, energy_rate_w=6.5),
        "balanced", 135, False, 78),
    "laptop-charging": (
        replace(BATTERY_BASE, status="Charging", capacity=62, energy_now_wh=36.0, power_w=18.0,
                voltage_v=12.9),
        (USB_PD,),
        UpowerReading(on_battery=False, is_battery=True, state=1, percentage=62.0,
                      energy_rate_w=18.0),
        "balanced", 45, True, 62),
    "laptop-limit": (
        replace(BATTERY_BASE, status="Not charging", capacity=80, energy_now_wh=46.4,
                power_w=0.0, voltage_v=12.8),
        (MAINS_ON,),
        UpowerReading(on_battery=False, is_battery=True, state=5, percentage=80.0),
        "power-saver", 300, True, 80),
}
DEMO_UPS = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_pct=32.0,
                      nominal_w=900.0, load_w=288.0, time_left_min=44.0, line_v=110.0,
                      model="示範不斷電系統")
DEMO_CPU_INFO = dict(name=DEMO_PROCESSOR, cores=8, threads=DEMO_THREADS)
DEMO_SENSORS = (dict(chip="示範感測器", label="主機板", value=38.0, device=""),)
DEMO_FANS = (dict(name="示範風扇", value=1150.0),)
DEMO_CPU_TEMP_C = 47.0
DEMO_DISK_TEMP_C = 41.0


def check(scenario):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown preview scenario: " + str(scenario))
    return scenario


def demo_gpus():
    return (GpuReading(vendor="nvidia", index=0, name="示範顯示卡", usage=DEMO_GPU_USAGE,
                       vram_used=3 * GIB, vram_total=24 * GIB, temp_c=45.0, fan_pct=30.0,
                       watts=61.5, stamp=time.monotonic()),)


def power_service(scenario):
    """示範用的電源服務：所有讀取函式都回傳固定的示範值。"""
    if check(scenario) == DESKTOP:
        return PowerService(read_ups=lambda: DEMO_UPS, read_gpus=demo_gpus,
                            processor=DEMO_PROCESSOR, model=DEMO_MODEL,
                            read_uptime=lambda: DEMO_UPTIME_SECONDS)
    battery, adapters, upower, profile = LAPTOPS[scenario][:4]
    return PowerService(read_ups=lambda: None, read_gpus=lambda: (), processor=DEMO_PROCESSOR,
                        model=DEMO_MODEL, read_uptime=lambda: DEMO_UPTIME_SECONDS,
                        read_batteries=lambda: (battery,),
                        read_adapters=lambda: adapters, read_upower=lambda: upower,
                        read_profile=lambda: profile)


def stamps(now):
    """從 7 天前到 now，每 10 分鐘一筆，最後一筆剛好是 now。"""
    count = History.RETENTION_SECONDS // STEP_SECONDS
    return [now - (count - index) * STEP_SECONDS for index in range(count + 1)]


def daily_plugged(stamp):
    hour = datetime.fromtimestamp(stamp).hour
    return any(start <= hour < end for start, end in PLUGGED_HOURS)


def wave(stamp, period):
    return math.sin(stamp / period)


def laptop_watts(stamp, plugged):
    """用電池時約 6.5 W；接電時量不到整機耗電（與實際筆電相同）。"""
    return None if plugged else round(6.5 + 1.5 * wave(stamp, 5400), 2)


def simulate(times, plugged):
    """依接電時段模擬電量：接電時充到 80% 維持，用電池時慢慢下降。"""
    pct, values = float(FIRST_PERCENT), []
    for flag in plugged:
        pct = min(HOLD_PERCENT, pct + CHARGE_PER_STEP) if flag else max(LOWEST_PERCENT,
                                                                       pct - DRAIN_PER_STEP)
        values.append(pct)
    return values


def finish_at(values, final_start, target):
    """最後一段改成從當時的電量平均變化到目前電量，讓圖表與標題一致。"""
    start = values[final_start - 1] if final_start else target
    steps = len(values) - final_start
    return values[:final_start] + [start + (target - start) * (index + 1) / steps
                                   for index in range(steps)]


def plug_schedule(times, now, minutes, final_plugged):
    """最後一段固定為目前的接電狀態，它之前的一小時相反，再之前依每天的接電時段。"""
    final = minutes * SECONDS_PER_MINUTE
    plugged = []
    for stamp in times:
        age = now - stamp
        if age < final:
            plugged.append(final_plugged)
        elif age < final + BEFORE_FINAL_SECONDS:
            plugged.append(not final_plugged)
        else:
            plugged.append(daily_plugged(stamp))
    return plugged


def laptop_history(scenario, now):
    battery, adapters, upower, _, minutes, final_plugged, target = LAPTOPS[scenario]
    times = stamps(now)
    plugged = plug_schedule(times, now, minutes, final_plugged)
    final_start = next(index for index, stamp in enumerate(times)
                       if now - stamp < minutes * SECONDS_PER_MINUTE)
    values = finish_at(simulate(times, plugged), final_start, target)
    watts = [laptop_watts(stamp, flag) for stamp, flag in zip(times, plugged)]
    # 最後一筆用目前的整機耗電，與執行後每分鐘記錄的值接得起來
    current = combine((battery,), adapters, upower.on_battery).system_w
    watts[-1] = None if current is None else round(current, 2)
    return tuple(HistorySample(t=stamp, pct=int(round(pct)), watts=value, plugged=flag,
                               charging=flag and round(pct) < HOLD_PERCENT, ups_pct=None,
                               ups_online=None)
                 for stamp, pct, flag, value in zip(times, values, plugged, watts))


def desktop_history(now):
    """上班時段約 300 W、其他時間約 120 W；最後一筆用目前的示範讀值。"""
    rows = []
    for stamp in stamps(now):
        hour = datetime.fromtimestamp(stamp).hour
        base = 300 if BUSY_HOURS[0] <= hour < BUSY_HOURS[1] else 120
        watts = DEMO_UPS.load_w if stamp == now else round(base + 40 * wave(stamp, 1380), 2)
        rows.append(HistorySample(t=stamp, pct=None, watts=watts, plugged=True,
                                  charging=False, ups_pct=100.0, ups_online=True))
    return tuple(rows)


def demo_history(scenario, now):
    """合成的 7 天電量與耗電歷史（每 10 分鐘一筆）。"""
    if check(scenario) == DESKTOP:
        return desktop_history(now)
    return laptop_history(scenario, now)


def demo_devices():
    now = time.time()
    return [
        dict(id="demo:mouse", name="示範無線滑鼠", kind="無線周邊", status="已連線",
             battery="80%", percent=80, stamp=now, source="示範", detail="示範資料"),
        dict(id="demo:headset", name="示範藍牙耳機", kind="藍牙", status="已連線",
             battery="55%", percent=55, stamp=now, source="示範", detail="示範資料"),
        dict(id="demo:receiver", name="示範 USB 接收器", kind="USB", status="已連接",
             battery="電量不適用／未回報", percent=None, stamp=0, source="示範", detail="示範資料"),
    ]


def preview_profile(scenario):
    """示範的電源模式：筆電依情境，桌機為「平衡」。"""
    if check(scenario) == DESKTOP:
        return "balanced"
    return LAPTOPS[scenario][3]


def preview_settings():
    """示範模式不讀設定檔：一律使用預設值。"""
    return Settings(LEGACY_DEFAULTS, Preferences.defaults(), first_run=False)


class DemoStream:
    """取代 nvidia-smi 串流：示範模式不啟動任何程序。"""

    def start(self):
        return None

    def stop(self, timeout=None):
        return None

    def latest(self):
        return ()

    def pending(self, now=None):
        return False


def demo_system(now):
    """總覽頁的即時資料（示範值，隨時間小幅變化，曲線才看得出來）。"""
    rx, tx = demo_rates(now)
    ram_used = demo_ram_used(now)
    networks = [dict(name=WIRED, rx=rx, tx=tx, physical=True, kind="有線網路"),
                dict(name=VIRTUAL, rx=0.0, tx=0.0, physical=False, kind="虛擬介面")]
    return dict(cpu=demo_cpu_percent(now), cpu_info=DEMO_CPU_INFO,
                ram=ram_used / DEMO_RAM_BYTES * 100, ram_used=ram_used,
                ram_total=DEMO_RAM_BYTES, networks=networks, rx=rx, tx=tx,
                disks=[dict(name=disk["name"], read=0.0 if disk["external"] else rx / 3,
                            write=0.0 if disk["external"] else tx / 2) for disk in DEMO_DISKS],
                stamp=now)


class PreviewCollectors(Collectors):
    """不呼叫 Collectors.__init__：不讀裝置別名、不連系統匯流排、不建立 Solaar 查詢，
    也不讀這台電腦的 /proc 與 /sys。"""

    def __init__(self, scenario):
        self.aliases, self.logitech_battery, self.bus, self.updates = {}, None, None, {}
        self.cpu_info = DEMO_CPU_INFO
        self.disk_meta, self.last_disk_meta = list(DEMO_DISKS), None
        self.nvidia = DemoStream()
        self.power = power_service(scenario)

    def system(self):
        return demo_system(time.time())

    def slow(self):
        power = self.power.sample()
        gpu = primary_gpu(power.gpus)
        return dict(gpu=gpu, gpus=power.gpus, gpu_error="" if gpu else GPU_UNAVAILABLE,
                    ups=power.ups, power=power, temperatures=list(DEMO_SENSORS),
                    cpu_temp=DEMO_CPU_TEMP_C, cpu_temp_label="示範處理器溫度",
                    fans=list(DEMO_FANS),
                    storage=[dict(disk, temperature=None if disk["external"] else DEMO_DISK_TEMP_C)
                             for disk in self.disk_meta],
                    network_temperatures={}, stamp=time.time())

    def refresh_disk_meta(self):
        return None

    def detail(self, request):
        """系統監控頁的示範資料；與總覽頁使用同一個時間點。"""
        return demo_detail(request, time.time(), time.monotonic())

    @staticmethod
    def histories(stamp):
        """系統監控頁第一次顯示時預先填好的曲線（stamp 為單調時鐘秒數）。"""
        return demo_histories(stamp, time.time())

    def logitech(self):
        return {}

    def devices(self):
        return dict(devices=demo_devices(), errors=[], stamp=time.time())
