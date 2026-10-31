"""看板的補回（Monitor 的混入類別）：由 UPower 自己的電量紀錄補回本程式沒有紀錄的時段。

- 每記錄一筆自己的紀錄就檢查一次（啟動後第一筆、睡眠喚醒後第一筆都會留下新的空白）；
  讀取一律非同步，經由收集器的系統匯流排連線（見 sdm.services.backfill_service）。
- 示範模式不建立（示範資料只在記憶體，絕不讀 UPower）；模擬測試只有注入 backfill_reader 時才建立。
- 補回後重畫電源頁的歷史圖；結束時取消進行中的讀取。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
from sdm.services.backfill_service import BackfillService
from sdm.services.upower_history import UpowerHistoryReader


class BackfillMixin:
    backfill = None
    backfill_reader = None      # 模擬測試可注入假的讀取器；一般執行為 None（讀系統的 UPower）

    def start_backfill(self):
        self.backfill = self.make_backfill()

    def make_backfill(self):
        if self.preview or self.history is None:
            return None
        reader = self.backfill_reader
        if reader is None:
            if self.smoke:
                return None
            reader = UpowerHistoryReader(self.backfill_bus)
        return BackfillService(self.history, reader, self.backfill_changed)

    def backfill_bus(self):
        return getattr(getattr(self, "collector", None), "bus", None)

    def check_backfill(self):
        if self.backfill is not None and not self.closing:
            self.backfill.check()

    def backfill_changed(self):
        if self.power_page is not None and not self.closing:
            self.power_page.history_changed()

    def stop_backfill(self):
        if self.backfill is not None:
            self.backfill.stop()
