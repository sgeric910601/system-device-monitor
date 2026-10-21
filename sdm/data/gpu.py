"""顯示卡唯讀讀取：NVIDIA 以單一常駐 nvidia-smi 串流，AMD 讀 sysfs。

AMD 內建顯示（APU）的 power1 標籤為 PPT，代表整顆處理器插槽的功耗，
單位尚未校正，所以只保留原始值 ppt_raw，不當成 CPU 或 GPU 功耗顯示。
"""
from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Optional

from sdm.constants import Collect, Gpu, Power
from sdm.core.fmt import number
from sdm.data.sources import clean_text, read

LOG = logging.getLogger(__name__)
MIB = 1024 * 1024
MICROWATTS = 1_000_000
QUERY = ("index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
         "fan.speed,power.draw")
VALUE_FIELDS = 6                     # name 之後的數值欄位數
INDEX = re.compile(r"[0-9]+")
CARD = re.compile(r"card([0-9]+)")


@dataclass(frozen=True)
class GpuReading:
    """單張顯示卡的讀值；顯示記憶體以位元組計，stamp 為單調時鐘秒數。"""
    vendor: str
    index: int
    name: str
    integrated: bool = False
    usage: Optional[float] = None
    vram_used: Optional[int] = None
    vram_total: Optional[int] = None
    temp_c: Optional[float] = None
    fan_pct: Optional[float] = None
    watts: Optional[float] = None
    ppt_raw: Optional[float] = None  # AMD APU 的 PPT 原始值，待第 6 階段校正
    stamp: float = 0.0

    @property
    def gpu_id(self):
        return f"gpu:{self.vendor}:{self.index}"


def within(value, low, high):
    return value if value is not None and low <= value <= high else None


def percent_value(text):
    return within(number(text), 0, 100)


def temperature_value(value):
    return within(value, Gpu.TEMP_MIN_C, Gpu.TEMP_MAX_C)


def byte_value(text, scale=1):
    value = within(number(text), 0, math.inf)
    return None if value is None else int(value * scale)


def parse_nvidia_line(line, stamp):
    """解析一行 CSV；[N/A]、[Not Supported] 或範圍外的值為 None，格式錯誤回傳 None。"""
    parts = line.split(",")
    if len(parts) < VALUE_FIELDS + 2:
        return None
    head = parts[0].strip()
    name = clean_text(",".join(parts[1:-VALUE_FIELDS]), Gpu.NAME_MAX_CHARS)
    if not INDEX.fullmatch(head) or not name:
        return None
    usage, used, total, temp, fan, watts = (part.strip() for part in parts[-VALUE_FIELDS:])
    return GpuReading(
        vendor="nvidia", index=int(head), name=name, usage=percent_value(usage),
        vram_used=byte_value(used, MIB), vram_total=byte_value(total, MIB),
        temp_c=temperature_value(number(temp)), fan_pct=percent_value(fan),
        watts=within(number(watts), 0, math.inf), stamp=stamp)


def terminate(process):
    """結束並回收子程序，避免留下殭屍程序。"""
    process.terminate()
    try:
        process.wait(Gpu.STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class NvidiaStream:
    """一個常駐的 nvidia-smi -lms 程序；背景執行緒保存每張卡最新一行。

    程序結束後只在 start() 之後、stop() 之前重新啟動，等待時間從 1 秒加倍到 60 秒。
    wait(delay) 可替換以便測試：回傳 True 代表停止重啟。
    """

    def __init__(self, command=Collect.NVIDIA_SMI, interval_ms=Gpu.STREAM_INTERVAL_MS,
                 clock=time.monotonic, wait=None):
        self.command, self.interval_ms, self.clock = command, int(interval_ms), clock
        self.stale_after = self.interval_ms / 1000 * Power.STALE_FACTOR
        self._wait = wait
        self._lock = threading.Lock()
        self._latest = {}
        self._stop = threading.Event()
        self._thread = None
        self._process = None
        self._started_at = None

    def args(self):
        return [self.command, "--query-gpu=" + QUERY, "--format=csv,noheader,nounits",
                "-lms", str(self.interval_ms)]

    def start(self):
        """啟動串流；找不到可執行檔時回傳 False 且不建立執行緒。"""
        if self.running():
            return True
        if not os.access(self.command, os.X_OK):
            LOG.info("nvidia-smi not available; NVIDIA readings disabled")
            return False
        self._stop.clear()
        self._started_at = self.clock()
        self._thread = threading.Thread(target=self._run, name="nvidia-stream", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """停止重啟、結束子程序並等待執行緒結束。"""
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None:
            terminate(process)
        if self._thread is not None:
            self._thread.join(Gpu.STOP_TIMEOUT)
        self._started_at = None

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def pending(self, now=None):
        """已啟動但還沒收到第一行，且仍在等待期內。"""
        started = self._started_at
        if started is None or not self.running():
            return False
        now = self.clock() if now is None else now
        received = any(reading.stamp >= started for reading in self.latest())
        return not received and now - started <= self.stale_after

    def accept_line(self, line):
        reading = parse_nvidia_line(line, self.clock())
        if reading is not None:
            with self._lock:
                self._latest = {**self._latest, reading.index: reading}

    def latest(self):
        """每張卡最後一次讀值（含過期），依編號排序。"""
        with self._lock:
            values = list(self._latest.values())
        return tuple(sorted(values, key=lambda reading: reading.index))

    def readings(self, now=None):
        """只回傳未過期（不超過 3 倍間隔）的讀值。"""
        now = self.clock() if now is None else now
        return tuple(reading for reading in self.latest()
                     if now - reading.stamp <= self.stale_after)

    @staticmethod
    def next_backoff(value):
        return min(value * 2, Gpu.BACKOFF_MAX)

    def _sleep(self, delay):
        if self._wait is not None:
            return bool(self._wait(delay)) or self._stop.is_set()
        return self._stop.wait(delay)

    def _run(self):
        backoff = Gpu.BACKOFF_INITIAL
        try:
            while not self._stop.is_set():
                began = self.clock()
                self._run_once()
                if self._stop.is_set():
                    break
                if self.clock() - began >= Gpu.BACKOFF_RESET_SECONDS:
                    backoff = Gpu.BACKOFF_INITIAL
                LOG.info("nvidia-smi stream ended; restarting in %d s", backoff)
                if self._sleep(backoff):
                    break
                backoff = self.next_backoff(backoff)
        except Exception as error:  # 執行緒不能無聲結束
            LOG.warning("NVIDIA stream stopped: %s", type(error).__name__)

    def _run_once(self):
        try:
            process = subprocess.Popen(
                self.args(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"},
                text=True, encoding="utf-8", errors="replace", close_fds=True)
        except OSError as error:
            LOG.warning("Cannot start nvidia-smi: %s", type(error).__name__)
            return
        with self._lock:
            self._process = process
        try:
            if not self._stop.is_set():
                for line in process.stdout:
                    self.accept_line(line)
        finally:
            terminate(process)
            process.stdout.close()
            with self._lock:
                self._process = None


def card_devices(root):
    """依編號排序的 AMD 顯示卡 device 目錄；略過 card1-DP-1 這類輸出接頭。"""
    drm = Path(root)/"sys/class/drm"
    try:
        cards = [entry for entry in drm.iterdir() if CARD.fullmatch(entry.name)]
    except OSError:
        return []
    cards.sort(key=lambda entry: int(CARD.fullmatch(entry.name).group(1)))
    return [card/"device" for card in cards
            if read(card/"device"/"vendor").lower() == Gpu.AMD_VENDOR]


def first_hwmon(device):
    found = sorted((device/"hwmon").glob("hwmon*"))
    return found[0] if found else None


def amd_power_raw(hwmon):
    """power1_average 優先，否則 power1_input；單位為驅動回報的原始值（獨顯為微瓦）。"""
    for name in ("power1_average", "power1_input"):
        value = within(number(read(hwmon/name)), 0, math.inf)
        if value is not None:
            return value
    return None


def amd_fan(hwmon):
    pwm = number(read(hwmon/"pwm1"))
    top = number(read(hwmon/"pwm1_max")) or Gpu.PWM_MAX
    return None if pwm is None else within(pwm / top * 100, 0, 100)


def amd_reading(device, index, now):
    hwmon = first_hwmon(device)
    total = byte_value(read(device/"mem_info_vram_total"))
    label = read(hwmon/"power1_label") if hwmon else ""
    raw = amd_power_raw(hwmon) if hwmon else None
    integrated = "PPT" in label.upper() or (total is not None and total <= Gpu.INTEGRATED_VRAM_MAX)
    temp = number(read(hwmon/"temp1_input")) if hwmon else None
    return GpuReading(
        vendor="amd", index=index, name="AMD 內建顯示" if integrated else "AMD 顯示卡",
        integrated=integrated, usage=percent_value(read(device/"gpu_busy_percent")),
        vram_used=byte_value(read(device/"mem_info_vram_used")), vram_total=total,
        temp_c=temperature_value(None if temp is None else temp / 1000),
        fan_pct=amd_fan(hwmon) if hwmon else None,
        watts=None if integrated or raw is None else raw / MICROWATTS,
        ppt_raw=raw if integrated else None, stamp=now)


def read_amd_gpus(root=Path("/"), now=None):
    """讀取 root 之下所有 AMD 顯示卡；root 可指向假的 /sys 目錄以便測試。"""
    now = time.monotonic() if now is None else now
    return tuple(amd_reading(device, index, now)
                 for index, device in enumerate(card_devices(root)))


def primary_gpu(gpus):
    """總覽卡片顯示的顯示卡：第一張 NVIDIA，否則第一張 AMD 獨顯。"""
    ordered = sorted(gpus, key=lambda gpu: gpu.index)
    nvidia = [gpu for gpu in ordered if gpu.vendor == "nvidia"]
    if nvidia:
        return nvidia[0]
    return next((gpu for gpu in ordered if gpu.vendor == "amd" and not gpu.integrated), None)
