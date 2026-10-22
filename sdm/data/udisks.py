"""UDisks2 的 SMART 唯讀讀取（系統匯流排，不需要管理員權限）。

- 區塊裝置 → 磁碟物件：Block.Drive；磁碟物件的 NVMe.Controller 或 Drive.Ata 介面提供 SMART。
- 只逐一 Get 列在 READ_PROPERTIES 的屬性（不用 GetAll），絕不讀序號、WWN 或識別碼；
  NVMe 另呼叫 SmartGetAttributes 取得已用壽命與出廠以來讀寫量（不要求互動授權）。
- 屬性每 60 秒、SmartGetAttributes 每 10 分鐘讀一次，其餘時間用快取。
- 任何一個呼叫逾時（1.8 秒）就略過這次其餘的呼叫，30 秒內不再呼叫。
get／call 可替換，測試以假的匯流排代替。
"""
from dataclasses import replace
import logging
import threading
import time

from gi.repository import Gio, GLib

from sdm.constants import Collect, Disk
from sdm.core.disk_health import ATA, NONE, NVME, DiskSmart, kelvin_to_celsius, \
    valid_block_name
from sdm.data.sources import clean_text
from sdm.data.upower import bus_getter, is_timeout

LOG = logging.getLogger(__name__)
DESTINATION = "org.freedesktop.UDisks2"
BLOCK_PATH = "/org/freedesktop/UDisks2/block_devices/"
BLOCK_INTERFACE = "org.freedesktop.UDisks2.Block"
NVME_INTERFACE = "org.freedesktop.UDisks2.NVMe.Controller"
ATA_INTERFACE = "org.freedesktop.UDisks2.Drive.Ata"
NO_DRIVE = "/"
NVME_PROPERTIES = ("SmartUpdated", "SmartCriticalWarning", "SmartPowerOnHours",
                   "SmartTemperature")
ATA_PROPERTIES = ("SmartSupported", "SmartEnabled", "SmartUpdated", "SmartFailing",
                  "SmartNumBadSectors", "SmartPowerOnSeconds", "SmartTemperature")
READ_PROPERTIES = ("Drive",) + NVME_PROPERTIES + ATA_PROPERTIES
WARNING_MAX_CHARS = 32
WARNING_MAX_ITEMS = 8
SECONDS_PER_HOUR = 3600
MAX_COUNTER = 2 ** 63
MAX_BYTE_VALUE = 255
MAX_HOURS = 10 ** 7

__all__ = ("SmartSource", "DiskSmart", "bus_getter", "bus_caller")


class Timeout(Exception):
    """某個呼叫逾時：這次取樣不再呼叫。"""


def bus_caller(bus):
    """系統匯流排的方法呼叫函式；bus 為 None 時一律失敗。不自動啟動服務。"""
    def call(destination, path, interface, method, args):
        if bus is None:
            raise GLib.Error("System bus unavailable")
        return bus.call_sync(destination, path, interface, method, args, None,
                             Gio.DBusCallFlags.NO_AUTO_START, Collect.DBUS_TIMEOUT_MS,
                             None).unpack()
    return call


def integer(value, high=MAX_COUNTER):
    real = isinstance(value, int) and not isinstance(value, bool)
    return value if real and 0 <= value <= high else None


def boolean(value):
    return value if isinstance(value, bool) else None


def warnings(value):
    if not isinstance(value, (list, tuple)):
        return ()
    cleaned = (clean_text(item, WARNING_MAX_CHARS) for item in value if isinstance(item, str))
    return tuple(item for item in cleaned if item)[:WARNING_MAX_ITEMS]


def nvme_attributes(values):
    """SmartGetAttributes 的結果 → DiskSmart 的欄位（已用壽命、備用區塊、讀寫量、錯誤）。"""
    values = values if isinstance(values, dict) else {}
    return dict(percent_used=integer(values.get("percent_used"), MAX_BYTE_VALUE),
                avail_spare=integer(values.get("avail_spare"), MAX_BYTE_VALUE),
                spare_thresh=integer(values.get("spare_thresh"), MAX_BYTE_VALUE),
                bytes_read=integer(values.get("total_data_read")),
                bytes_written=integer(values.get("total_data_written")),
                media_errors=integer(values.get("media_errors")))


class SmartSource:
    def __init__(self, get, call, clock=time.monotonic):
        self.get, self.call, self.clock = get, call, clock
        self.lock = threading.Lock()
        self.results, self.read_at, self.drives = {}, {}, {}
        self.attributes = {}            # 名稱 →（欄位、讀取時間）
        self.retry_at = None

    def fetch(self, path, interface, name):
        try:
            return self.get(DESTINATION, path, interface, name)
        except GLib.Error as error:
            if is_timeout(error):
                raise Timeout() from None
            raise

    def optional(self, path, interface, name):
        """讀不到（不是逾時）的屬性為 None。"""
        try:
            return self.fetch(path, interface, name)
        except GLib.Error:
            return None

    def refresh(self, names, attributes=False):
        """依間隔更新 names 的 SMART；回傳名稱 → DiskSmart（含快取）。"""
        names = tuple(name for name in names if valid_block_name(name))
        now = self.clock()
        self.forget(names)
        if self.retry_at is None or now >= self.retry_at:
            self.retry_at = None
            try:
                for name in names:
                    self.refresh_disk(name, attributes, now)
            except Timeout:
                self.retry_at = now + Disk.SMART_BACKOFF_SECONDS
                LOG.debug("UDisks2 call timed out; retrying in %d s", Disk.SMART_BACKOFF_SECONDS)
        with self.lock:
            return {name: self.results[name] for name in names if name in self.results}

    def forget(self, names):
        """已經不在清單上的磁碟：丟掉快取，裝置名稱被別的磁碟重用時才不會沿用舊結果。"""
        with self.lock:
            for table in (self.results, self.read_at, self.drives, self.attributes):
                for name in [key for key in table if key not in names]:
                    del table[name]

    def cached(self, name):
        with self.lock:
            return self.results.get(name)

    def refresh_disk(self, name, attributes, now):
        if now - self.read_at.get(name, -Disk.SMART_PROPERTIES_INTERVAL) >= \
                Disk.SMART_PROPERTIES_INTERVAL:
            drive = self.drive_path(name)
            smart = self.read_drive(drive)
            with self.lock:
                self.drives[name], self.read_at[name] = (drive, smart.kind), now
                self.results[name] = self.merged(name, smart)
        drive, kind = self.drives.get(name, (None, NONE))
        last = self.attributes.get(name, (None, -Disk.SMART_ATTRIBUTES_INTERVAL))[1]
        if attributes and kind == NVME and now - last >= Disk.SMART_ATTRIBUTES_INTERVAL:
            fields = self.read_attributes(drive)
            with self.lock:
                previous = self.attributes.get(name, (None, 0))[0]
                self.attributes[name] = (fields or previous, now)
                self.results[name] = self.merged(name, self.results[name])

    def merged(self, name, smart):
        fields = self.attributes.get(name, (None, 0))[0]
        return smart if not fields or smart.kind != NVME else replace(smart, **fields)

    def drive_path(self, name):
        path = self.optional(BLOCK_PATH + name, BLOCK_INTERFACE, "Drive")
        return path if isinstance(path, str) and path.startswith("/") and path != NO_DRIVE \
            else None

    def read_drive(self, drive):
        if drive is None:
            return DiskSmart(kind=NONE)
        return self.read_nvme(drive) or self.read_ata(drive) or DiskSmart(kind=NONE)

    def read_nvme(self, drive):
        try:
            updated = self.fetch(drive, NVME_INTERFACE, "SmartUpdated")
        except GLib.Error:
            return None
        def get(name):
            return self.optional(drive, NVME_INTERFACE, name)
        return DiskSmart(kind=NVME, updated=integer(updated),
                         critical_warning=warnings(get("SmartCriticalWarning")),
                         power_on_hours=integer(get("SmartPowerOnHours"), MAX_HOURS),
                         temperature_c=kelvin_to_celsius(get("SmartTemperature")))

    def read_ata(self, drive):
        try:
            supported = self.fetch(drive, ATA_INTERFACE, "SmartSupported")
        except GLib.Error:
            return None
        def get(name):
            return self.optional(drive, ATA_INTERFACE, name)
        seconds = integer(get("SmartPowerOnSeconds"))
        return DiskSmart(kind=ATA, supported=boolean(supported), enabled=boolean(get("SmartEnabled")),
                         updated=integer(get("SmartUpdated")),
                         failing=boolean(get("SmartFailing")),
                         bad_sectors=integer(get("SmartNumBadSectors")),
                         power_on_hours=None if seconds is None else seconds // SECONDS_PER_HOUR,
                         temperature_c=kelvin_to_celsius(get("SmartTemperature")))

    def read_attributes(self, drive):
        try:
            reply = self.call(DESTINATION, drive, NVME_INTERFACE, "SmartGetAttributes",
                              GLib.Variant("(a{sv})", ({},)))
        except GLib.Error as error:
            if is_timeout(error):
                raise Timeout() from None
            return None         # 這次讀不到：沿用上一次的結果，10 分鐘後再試
        return nvme_attributes(reply[0] if reply else {})
