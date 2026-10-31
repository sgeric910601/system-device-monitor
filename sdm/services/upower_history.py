"""非同步讀取 UPower 自己的電量紀錄（GetHistory），給補回空白時段使用。

- 只在主執行緒使用；全部是非同步的 Gio.DBusConnection.call，不卡住主迴圈。
- 不自動啟動沒有在執行的服務（NO_AUTO_START）；每個呼叫 1.8 秒逾時，整次讀取最多 15 秒，
  時間到就放棄這一次，之後收到的回覆一律忽略。
- 只呼叫 EnumerateDevices、每個裝置的 Type（電池再讀 PowerSupply）與 GetHistory("charge")；
  絕不讀序號、型號或原生路徑。裝置的物件路徑只在這次讀取中使用，不寫進日誌、結果或檔案
  （藍牙裝置的路徑可能含有硬體位址）；日誌只記 D-Bus 錯誤名稱，不記錯誤訊息。
- 筆電電池：類型為電池且 PowerSupply 為真的裝置剛好一個；不斷電系統：類型為不斷電系統的裝置
  剛好一個。沒有或不只一個時回報該系列 missing（多顆電池時本程式記錄的是合計的電量）。
  DisplayDevice 不提供紀錄（UPower 回覆 device does not support getting history），不使用。
connection() 回傳系統匯流排連線（沒有時為 None）；測試以假的連線或私人匯流排上的假 UPower 代替。
"""
from dataclasses import dataclass
import logging
from typing import Optional

from gi.repository import Gio, GLib

from sdm.constants import Backfill
from sdm.core.backfill import SERIES_BATTERY, SERIES_UPS
from sdm.data.upower import (DESTINATION, DEVICE_INTERFACE, MANAGER_INTERFACE, MANAGER_PATH,
                             PROPERTIES_INTERFACE, TYPE_BATTERY, is_timeout)
from sdm.services.sleep_gate import glib_cancel, glib_schedule

LOG = logging.getLogger(__name__)
TYPE_UPS = 3                    # UPower 裝置類型：不斷電系統
HISTORY_KIND = "charge"         # 電量紀錄（另有 rate、time-full、time-empty）
MAX_TIMESPAN = 2 ** 32 - 1      # GetHistory 的秒數是 32 位元無號整數
NO_BUS, DEADLINE, TIMEOUT = "no system bus", "deadline", "timeout"


@dataclass(frozen=True)
class FetchResult:
    points: tuple = ()              # 每個系列一組：（系列、GetHistory 的原始點）
    missing: tuple = ()             # 找不到唯一對應裝置、或裝置不提供紀錄的系列
    error: Optional[str] = None     # 整次失敗的原因（錯誤名稱）；成功時為 None


def error_name(error):
    """只取錯誤名稱：錯誤訊息可能含有裝置路徑。"""
    if is_timeout(error):
        return TIMEOUT
    return Gio.DBusError.get_remote_error(error) or "D-Bus error"


class HistoryRequest:
    """一次讀取：列出裝置 → 逐一讀類型 → 選出裝置 → 逐一讀紀錄 → 回報（只回報一次）。"""

    def __init__(self, bus, series, timespan, done, schedule, cancel):
        self.bus, self.series, self.done = bus, tuple(series), done
        self.timespan = max(0, min(MAX_TIMESPAN, int(timespan)))
        self.schedule, self.cancel = schedule, cancel
        self.cancellable = Gio.Cancellable()
        self.paths, self.index = (), 0
        self.found = {SERIES_BATTERY: [], SERIES_UPS: []}
        self.chosen, self.missing, self.points = [], [], []
        self.finished, self.timer = False, None

    def start(self):
        self.timer = self.schedule(Backfill.DEADLINE_SECONDS, self.expired)
        self.call(MANAGER_PATH, MANAGER_INTERFACE, "EnumerateDevices", None, "(ao)", self.listed,
                  self.failed)

    def call(self, path, interface, method, args, reply, on_reply, on_error):
        self.bus.call(DESTINATION, path, interface, method, args, GLib.VariantType(reply),
                      Gio.DBusCallFlags.NO_AUTO_START, Backfill.CALL_TIMEOUT_MS,
                      self.cancellable, self.replied, (on_reply, on_error))

    def replied(self, bus, result, handlers):
        if self.finished:
            return
        on_reply, on_error = handlers
        try:
            values = bus.call_finish(result).unpack()
        except GLib.Error as error:
            on_error(error)
            return
        on_reply(values[0])

    # ── 找裝置 ─────────────────────────────────────────────

    def listed(self, paths):
        valid = [path for path in paths if isinstance(path, str)] \
            if isinstance(paths, list) else []
        self.paths = tuple(valid[:Backfill.MAX_DEVICES])
        self.probe()

    def probe(self):
        if self.index >= len(self.paths):
            self.choose()
            return
        path = self.paths[self.index]
        self.get(path, "Type", lambda kind: self.typed(path, kind))

    def get(self, path, name, on_reply):
        self.call(path, PROPERTIES_INTERFACE, "Get", GLib.Variant("(ss)", (DEVICE_INTERFACE, name)),
                  "(v)", on_reply, self.skip)

    def typed(self, path, kind):
        if type(kind) is int and kind == TYPE_BATTERY and SERIES_BATTERY in self.series:
            self.get(path, "PowerSupply", lambda supply: self.powered(path, supply))
            return
        if type(kind) is int and kind == TYPE_UPS and SERIES_UPS in self.series:
            self.found[SERIES_UPS].append(path)
        self.next_device()

    def powered(self, path, supply):
        if supply is True:      # 周邊（滑鼠、耳機）的電池 PowerSupply 為假
            self.found[SERIES_BATTERY].append(path)
        self.next_device()

    def skip(self, error):
        """讀不到的裝置（例如剛拔掉）略過，繼續下一個。"""
        LOG.debug("Skipped a UPower device (%s)", error_name(error))
        self.next_device()

    def next_device(self):
        self.index += 1
        self.probe()

    # ── 讀紀錄 ─────────────────────────────────────────────

    def choose(self):
        for series in self.series:
            candidates = self.found.get(series, [])
            if len(candidates) == 1:
                self.chosen.append((series, candidates[0]))
            else:
                self.missing.append(series)
        self.read_next()

    def read_next(self):
        if not self.chosen:
            self.finish(FetchResult(tuple(self.points), tuple(self.missing)))
            return
        series, path = self.chosen.pop(0)
        args = GLib.Variant("(suu)", (HISTORY_KIND, self.timespan, Backfill.MAX_POINTS))
        self.call(path, DEVICE_INTERFACE, "GetHistory", args, "(a(udu))",
                  lambda points: self.received(series, points),
                  lambda error: self.history_failed(series, error))

    def received(self, series, points):
        self.points.append((series, tuple(points)))
        self.read_next()

    def history_failed(self, series, error):
        """逾時代表服務太慢，整次放棄；其他錯誤（裝置不提供紀錄）只略過這個系列。"""
        if is_timeout(error):
            self.failed(error)
            return
        LOG.debug("UPower history unavailable for %s (%s)", series, error_name(error))
        self.missing.append(series)
        self.read_next()

    # ── 結束 ───────────────────────────────────────────────

    def failed(self, error):
        self.finish(FetchResult(error=error_name(error)))

    def expired(self):
        self.timer = None
        self.finish(FetchResult(error=DEADLINE))
        return False

    def finish(self, result):
        if self.finished:
            return
        self.abort()
        self.done(result)

    def abort(self):
        """停止：取消時限與還沒回覆的呼叫，之後的回覆都忽略。"""
        self.finished = True
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None
        self.cancellable.cancel()


class UpowerHistoryReader:
    """同一時間只有一次讀取；新的讀取會取代還沒完成的那一次（舊的不回報）。"""

    def __init__(self, connection, schedule=glib_schedule, cancel=glib_cancel):
        self.connection, self.schedule, self.cancel = connection, schedule, cancel
        self.request = None

    def fetch(self, series, timespan, done):
        """讀取 series 各自的電量紀錄；完成、失敗或逾時都只呼叫一次 done(FetchResult)。"""
        self.stop()
        bus = self.connection()
        if bus is None:
            done(FetchResult(error=NO_BUS))
            return
        self.request = HistoryRequest(bus, series, timespan, done, self.schedule, self.cancel)
        self.request.start()

    def stop(self):
        """放棄進行中的讀取（不回報）。"""
        if self.request is not None:
            self.request.abort()
            self.request = None
