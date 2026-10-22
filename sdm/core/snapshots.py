"""各系統監控頁的詳細資料快照（不可變）；由詳細取樣產生，介面只讀取。"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CpuDetail:
    sample: Optional[object]            # cpu.CpuSample；第一次取樣只建立基準時為 None
    load: Optional[tuple]               # 1、5、15 分鐘平均負載
    frequency_mhz: Optional[float]


@dataclass(frozen=True)
class ThermalDetail:
    groups: tuple                       # （群組、thermal.Stat），依顯示順序
    fans: tuple                         # thermal.Fan


@dataclass(frozen=True)
class AppsDetail:
    sample: object                      # apps.AppsSample
    cores: int
    has_cpu: bool = True                # 這次讀了 CPU 時間（CPU／GPU 頁）
    has_memory: bool = True             # 這次讀了記憶體（記憶體頁）
    labels: tuple = ()                  # 示範模式：（代號、名稱、圖示名稱）；實際資料由介面查詢


@dataclass(frozen=True)
class MemoryDetail:
    composition: Optional[object]       # memory.MemoryComposition
    pressure: Optional[object]          # memory.Pressure
    level: str


@dataclass(frozen=True)
class VolumeStatus:
    mount: str
    fstype: str
    total: int
    used: int
    available: int


@dataclass(frozen=True)
class DiskStatus:
    name: str
    model: str
    connection: str
    external: bool
    size: int
    volumes: tuple
    read_rate: Optional[float] = None
    write_rate: Optional[float] = None
    read_total: Optional[int] = None    # 開機以來
    write_total: Optional[int] = None
    temperature: Optional[float] = None
    smart: Optional[object] = None      # disk_health.DiskSmart


@dataclass(frozen=True)
class InterfaceStatus:
    name: str
    kind: str
    physical: bool
    rx_rate: Optional[float]
    tx_rate: Optional[float]
    rx_total: int                       # 開機（或介面建立）以來
    tx_total: int


@dataclass(frozen=True)
class NetworkDetail:
    primary: Optional[InterfaceStatus]
    address: Optional[str]
    rx_rate: Optional[float]            # 主要介面；換介面時重新建立基準
    tx_rate: Optional[float]
    changed: bool
    others: tuple                       # 其他連線中的介面


@dataclass(frozen=True)
class DetailSnapshot:
    stamp: float                        # 單調時鐘秒數
    cpu: Optional[CpuDetail] = None
    thermal: Optional[ThermalDetail] = None
    apps: Optional[AppsDetail] = None
    memory: Optional[MemoryDetail] = None
    disks: Optional[tuple] = None       # DiskStatus
    network: Optional[NetworkDetail] = None
