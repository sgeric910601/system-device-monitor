"""「電源」頁的耗電排行：有 CPU 功耗時依 CPU 佔比估算瓦數，沒有時依 CPU 使用率排序。"""
import unittest

from sdm.core.apps import AppsSample, AppUsage
from sdm.core.snapshots import AppsDetail
from sdm.ui.app_names import AppNames
from sdm.ui.ranking import CPU_NOTE, ESTIMATE_NOTE, power_ranking

LABELS = (("demo.browser", "示範瀏覽器", "web-browser"), ("demo.editor", "示範編輯器", "editor"),
          ("demo.player", "示範播放器", "player"))


def apps(ready=True):
    usages = (AppUsage("demo.browser", 60.0, 3.75, None), AppUsage("demo.editor", 20.0, 1.25, None),
              AppUsage("demo.player", 0.0, 0.0, None), AppUsage("demo.idle", None, None, None))
    return AppsDetail(AppsSample(usages, ready), cores=16, labels=LABELS)


class Ranking(unittest.TestCase):
    def setUp(self):
        self.names = AppNames(lambda app_id: None)

    def test_cpu_percent_when_cpu_watts_are_unknown(self):
        rows, note, empty = power_ranking(apps(), self.names)
        self.assertEqual([(row[0], row[2]) for row in rows],
                         [("示範瀏覽器", "60.0%"), ("示範編輯器", "20.0%")])
        self.assertEqual(note, CPU_NOTE)
        self.assertEqual(note, "目前無法取得 CPU 功耗，依 CPU 使用率排序")

    def test_estimated_watts_split_the_cpu_power_by_share(self):
        rows, note, _ = power_ranking(apps(), self.names, cpu_watts=40.0)
        self.assertEqual([(row[0], row[2], row[3]) for row in rows],
                         [("示範瀏覽器", "30.0 W", "CPU 60.0%"), ("示範編輯器", "10.0 W", "CPU 20.0%")])
        self.assertEqual(note, ESTIMATE_NOTE)
        self.assertEqual(note, "依 CPU 佔比估算")

    def test_waiting_for_the_first_sample(self):
        rows, _, empty = power_ranking(None, self.names)
        self.assertEqual((rows, empty), ((), "計算中…"))
        rows, _, empty = power_ranking(apps(ready=False), self.names)
        self.assertEqual(empty, "計算中…")


if __name__ == "__main__":
    unittest.main()
