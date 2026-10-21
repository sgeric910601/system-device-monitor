"""電源頁、頂端列與選單的文字：不需要顯示器即可測試。"""
import dataclasses
import unittest

from sdm.core.power_breakdown import make_breakdown
from sdm.ui.power_text import (TONE_CRITICAL, TONE_NORMAL, TONE_WARNING, breakdown_rows,
                                 detail_rows, duration_text, flow_model, header_text,
                                 menu_line, tray_label, uptime_text, watts_short, watts_text)
from sdm.data.gpu import GpuReading
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService

UPS = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_pct=32.0,
                 nominal_w=900.0, load_w=288.0, time_left_min=44.2, line_v=118.0,
                 model="Example UPS")
GPU = GpuReading(vendor="nvidia", index=0, name="Example GPU", watts=61.5, stamp=100.0)


def snapshot(ups=UPS, gpus=(GPU,), average=None):
    service = PowerService(read_ups=lambda: ups, read_gpus=lambda: gpus,
                           processor="Example CPU", model="Example Board",
                           read_uptime=lambda: (3 * 24 + 4) * 3600 + 59, clock=lambda: 100.0,
                           wall=lambda: 1.0)
    power = service.sample()
    return power if average is None else dataclasses.replace(power, average_w=average)


class Numbers(unittest.TestCase):
    def test_watts(self):
        self.assertEqual(watts_short(287.6), "288 W")
        self.assertEqual(watts_short(None), "—")
        self.assertEqual(watts_text(226.5), "226.5 W")
        self.assertEqual(watts_text(None), "—")

    def test_durations_follow_the_reference_format(self):
        self.assertEqual([duration_text(m) for m in (0, 44, 60, 125, None, -1)],
                         ["0 分", "44 分", "1 小時", "2 小時 5 分", "—", "—"])
        self.assertEqual(duration_text(44.6), "45 分")

    def test_uptime(self):
        self.assertEqual(uptime_text((3 * 24 + 4) * 3600 + 59), "3 天 4 小時")
        self.assertEqual(uptime_text(5 * 3600 + 3 * 60), "5 小時 3 分")
        self.assertEqual(uptime_text(90), "1 分")
        self.assertEqual(uptime_text(None), "—")


class Header(unittest.TestCase):
    def test_mains_with_average(self):
        self.assertEqual(header_text(snapshot(average=280.4)),
                         ("288 W", "3 分鐘平均 280 W · 不斷電系統輸出（含螢幕等所有插在上面的設備）",
                          TONE_NORMAL))

    def test_on_ups_battery(self):
        ups = dataclasses.replace(UPS, status=("ONBATT",), online=False, on_battery=True,
                                  time_left_min=25.6)
        self.assertEqual(header_text(snapshot(ups=ups)),
                         ("288 W", "改由不斷電系統電池供電 · 剩餘約 26 分鐘", TONE_WARNING))
        low = dataclasses.replace(ups, low_battery=True, time_left_min=None)
        self.assertEqual(header_text(snapshot(ups=low))[1:],
                         ("改由不斷電系統電池供電", TONE_CRITICAL))

    def test_without_ups(self):
        self.assertEqual(header_text(snapshot(ups=None)),
                         ("62 W", "無法取得整機耗電 · 顯示已知元件合計", TONE_NORMAL))
        self.assertEqual(header_text(snapshot(ups=None, gpus=()))[:2], ("—", "無法取得整機耗電"))
        self.assertEqual(header_text(None)[:2], ("—", "正在讀取耗電…"))


class Tray(unittest.TestCase):
    def test_tray_label(self):
        self.assertEqual(tray_label(snapshot()), "288 W")
        self.assertEqual(tray_label(None), "— W")

    def test_menu_line(self):
        self.assertEqual(menu_line(snapshot(average=280.4)), "耗電：288 W（3 分鐘平均 280 W）")
        self.assertEqual(menu_line(snapshot(ups=None)), "耗電：62 W（已知元件合計）")
        self.assertEqual(menu_line(snapshot(ups=None, gpus=())), "耗電：無法取得")
        self.assertEqual(menu_line(None), "耗電：讀取中")


class Breakdown(unittest.TestCase):
    def test_rows_with_share_of_the_total(self):
        rows = breakdown_rows(make_breakdown(288.0, (), 0.0))
        self.assertEqual(rows, (("other", "其他", "288.0 W", "100%"),))
        power = snapshot()
        self.assertEqual(breakdown_rows(power.breakdown),
                         (("gpu:nvidia:0", "Example GPU", "61.5 W", "21%"),
                          ("other", "其他", "226.5 W", "79%")))

    def test_rows_without_a_system_value_have_no_share(self):
        rows = breakdown_rows(snapshot(ups=None).breakdown)
        self.assertEqual(rows, (("gpu:nvidia:0", "Example GPU", "61.5 W", ""),))

    def test_flow_model(self):
        model = flow_model(snapshot())
        self.assertEqual((model.source_label, model.total_label, model.total_w),
                         ("市電", "整機", 288.0))
        self.assertEqual(model.items, (("gpu:nvidia:0", "Example GPU", 61.5),
                                       ("other", "其他", 226.5)))
        ups = dataclasses.replace(UPS, online=False, on_battery=True)
        self.assertEqual(flow_model(snapshot(ups=ups)).source_label, "不斷電系統電池")
        self.assertEqual(flow_model(snapshot(ups=None)).total_label, "已知元件合計")
        self.assertIsNone(flow_model(snapshot(ups=None, gpus=())))
        self.assertIsNone(flow_model(None))


class Details(unittest.TestCase):
    def test_eight_rows_in_order(self):
        self.assertEqual(detail_rows(snapshot()), (
            ("處理器", "Example CPU"), ("機型", "Example Board"), ("開機時間", "3 天 4 小時"),
            ("不斷電系統型號", "Example UPS"), ("市電電壓", "118 V"), ("負載", "32%"),
            ("額定功率", "900 W"), ("預估續航", "44 分")))

    def test_without_ups(self):
        rows = dict(detail_rows(snapshot(ups=None)))
        self.assertEqual([rows[key] for key in ("不斷電系統型號", "市電電壓", "負載", "額定功率",
                                                "預估續航")], ["—"] * 5)
        self.assertEqual(dict(detail_rows(None))["處理器"], "—")


if __name__ == "__main__":
    unittest.main()
