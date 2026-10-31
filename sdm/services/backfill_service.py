"""補回空白時段的排程：每記錄一筆自己的紀錄後檢查，有還沒處理的空白才向 UPower 讀一次。

- 啟動後第一筆紀錄、睡眠喚醒（或程式停住）後第一筆紀錄都會留下新的空白，因此兩種情況都在
  記錄當下觸發；同一時間只有一次讀取（非同步，見 sdm.services.upower_history）。
- 讀到結果後只補當時要求的空白，並把這些空白記成已處理（存在歷史檔案）：每段空白只處理一次；
  這次執行讀過的空白另外記在記憶體，已處理的時段因筆數上限被丟掉時也不會馬上重讀。
- 上一次計畫是空的、而且沒有新空白（見 needs_plan）時不重新掃全部紀錄，每分鐘的檢查幾乎不花時間。
- 讀取失敗（UPower 沒有執行、太慢）時 5 分鐘、15 分鐘後再試，連續失敗 3 次就在這次執行放棄；
  只有部分系列逾時時，讀到的系列照常補上，逾時的系列之後再試。
- 找不到唯一的對應裝置、或 UPower 的數值與自己最新一筆相差太多時，這次執行不再讀這個系列。
- 只在主執行緒使用；示範模式與模擬測試不建立（見 sdm.ui.backfill_wiring）。
日誌只記系列名稱（pct、ups_pct）、數量與錯誤名稱，不記任何裝置資訊。
"""
import logging
import time

from sdm.constants import Backfill
from sdm.core.backfill import (accept, checked_state, matches, needs_plan, parse_points,
                               plan_backfill)

LOG = logging.getLogger(__name__)


class BackfillService:
    def __init__(self, history, reader, changed=None, clock=time.time,
                 monotonic=time.monotonic):
        self.history, self.reader = history, reader
        self.changed = changed or (lambda: None)
        self.clock, self.monotonic = clock, monotonic
        self.busy = self.closed = self.gave_up = False
        self.failures, self.retry_at = 0, None
        self.skipped = frozenset()
        self.attempted = frozenset()    # 這次執行讀過的（系列、空白結束時間）
        self.checked = None             # 上一次空的計畫時的狀態（見 needs_plan）

    def check(self):
        """有還沒處理的空白、而且沒有在等待再試時送出一次讀取；回傳是否送出。"""
        if self.busy or self.closed or self.gave_up or self.waiting():
            return False
        now, samples, imported = self.clock(), self.history.samples, self.history.imported
        if not needs_plan(self.checked, samples, imported, now, self.skipped):
            return False
        plan = plan_backfill(samples, imported, now, self.skipped, self.attempted)
        if not plan.requests:
            self.checked = checked_state(samples, imported, now, self.skipped)
            return False
        self.checked, self.busy = None, True
        self.reader.fetch(plan.series, plan.timespan, lambda result: self.received(plan, result))
        return True

    def waiting(self):
        return self.retry_at is not None and self.monotonic() < self.retry_at

    def received(self, plan, result):
        self.busy = False
        if self.closed:
            return
        try:
            self.process(plan, result)
        except Exception as error:      # 補上時出錯（例如存檔出問題）也延後再試，不會每分鐘重讀
            LOG.warning("Filling history from UPower failed (%s)", type(error).__name__)
            self.failed(type(error).__name__)

    def process(self, plan, result):
        if result.error is not None:
            self.failed(result.error)
            return
        for series in result.missing:
            self.skip(series, "no single matching UPower device")
        raw = dict(result.points)
        added = sum(self.apply(request, raw[request.series]) for request in plan.requests
                    if request.series in raw)
        if result.failed:
            self.failed("timeout for " + ", ".join(result.failed))
        else:
            self.failures, self.retry_at = 0, None
        if added:
            self.changed()

    def apply(self, request, raw):
        """補進這個系列要求的空白；回傳補回的點數。"""
        now = self.clock()
        points = parse_points(raw, now)
        if not matches(points, self.history.samples, request.series):
            self.skip(request.series, "UPower readings differ from the app's own")
            return 0
        samples = accept(request.series, points, request.gaps, now)
        self.history.add_imported(request.series, request.gaps, samples)
        # 補進去之後才記成這次讀過：出錯時這些空白在延後之後還會再讀
        self.attempted = self.attempted | {(request.series, gap.end) for gap in request.gaps}
        LOG.info("Filled %d gap(s) in the %s history with %d point(s) from UPower",
                 len(request.gaps), request.series, len(samples))
        return len(samples)

    def skip(self, series, reason):
        self.skipped = self.skipped | {series}
        LOG.info("Not filling the %s history this session: %s", series, reason)

    def failed(self, reason):
        """第一、二次失敗後延後再試；連續失敗達上限時這次執行放棄。"""
        self.failures += 1
        if self.failures >= Backfill.MAX_FAILURES:
            self.gave_up = True
            LOG.info("UPower history unavailable (%s); not trying again this session", reason)
            return
        delays = Backfill.RETRY_SECONDS
        self.retry_at = self.monotonic() + delays[min(self.failures, len(delays)) - 1]
        LOG.debug("UPower history unavailable (%s); will retry later", reason)

    def stop(self):
        """結束時呼叫：取消進行中的讀取，之後的結果與檢查都忽略。"""
        self.closed = True
        self.reader.stop()
