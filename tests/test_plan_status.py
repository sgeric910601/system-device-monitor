"""--charging-plan-status（sdm.services.plan_status）：只讀取排程檔、紀錄檔、電量歷史與 sysfs，
印出排程、目前的階段、下一次提高上限與喚醒的時間與最後 20 行紀錄；不載入 GTK、不連 logind、
不寫任何檔案（排程檔損壞時也不備份）。全部在暫存目錄進行。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

from sdm.constants import SmartCharging
from sdm.core import plan_format
from sdm.core.charging_plan import PRECISE_WAKE, READINESS_FIRST, Once, Weekly, make_plan
from sdm.services import plan_status
from tests.fixtures import sysfs

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")
NOW = 1_800_000_000.0           # 2027-01-15 16:00 台北


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base/"root"
        self.root.mkdir()
        self.plan_path = self.base/"data"/SmartCharging.PLAN_FILE
        self.log_path = self.base/"state"/SmartCharging.LOG_FILE
        self.history_path = self.base/"data/history.json"

    def status(self):
        return plan_status.build_status(self.plan_path, self.log_path, self.history_path,
                                        root=self.root, now=NOW, tz=TAIPEI)

    def write_plan(self, plan):
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_bytes(plan_format.encode_plan(plan))

    def files(self):
        return sorted(str(path.relative_to(self.base)) for path in self.base.rglob("*")
                      if path.is_file() and not str(path.relative_to(self.base)).startswith("root"))


class Status(Base):
    def test_no_plan_on_a_desktop(self):
        value = self.status()
        self.assertEqual((value["plan"], value["plan_file"], value["battery"], value["phase"]),
                         (None, "none", None, None))
        self.assertEqual(value["log"], [])
        self.assertEqual(self.files(), [])

    def test_waiting_plan_with_the_next_raise_and_wake(self):
        sysfs.thinkpad(self.root, online=1, status="Not charging", capacity=80)
        deadline = NOW + 12 * 3600
        self.write_plan(make_plan(Once(deadline), deadline, 100, 80, 90, PRECISE_WAKE, NOW))
        value = self.status()
        self.assertEqual(value["plan_file"], "ok")
        self.assertEqual(value["plan"]["kind"], "once")
        self.assertEqual((value["plan"]["target"], value["plan"]["hold"],
                          value["plan"]["restore"]), (100, 80, 90))
        self.assertEqual(value["battery"], {"percent": 80, "plugged": True, "limit": 80})
        self.assertEqual((value["phase"], value["phase_title"]), ("waiting", "等待開始"))
        self.assertEqual(value["required_minutes"], 108)
        # 12 小時後 04:00 完成、需要 108＋15 分鐘：01:57 開始，精準喚醒並提高上限在 01:56
        self.assertEqual(value["next_raise"], "2027-01-16T01:56:00+08:00")
        self.assertEqual(value["wake_at"], "2027-01-16T01:56:00+08:00")
        self.assertEqual(value["deadline"], "2027-01-16T04:00:00+08:00")
        self.assertEqual(value["desired_limit"], 80)

    def test_limits_are_fitted_to_the_slider_options_like_the_app(self):
        """目標是原本的上限 77%：提高時寫的是滑桿選項中不低於它的 80%，與看板相同。"""
        sysfs.thinkpad(self.root, online=1, status="Not charging", capacity=60)
        deadline = NOW + 1800
        self.write_plan(make_plan(Once(deadline), deadline, 77, 77, 77, READINESS_FIRST, NOW))
        value = self.status()
        self.assertEqual((value["phase"], value["desired_limit"]), ("late", 80))

    def test_weekly_plan_without_a_wake(self):
        sysfs.thinkpad(self.root, online=1, status="Not charging", capacity=80)
        plan = make_plan(Weekly(frozenset({1, 2, 3, 4, 5}), 8 * 60), NOW + 3600, 100, 80, 80,
                         READINESS_FIRST, NOW)
        self.write_plan(plan)
        value = self.status()
        self.assertEqual((value["plan"]["kind"], value["plan"]["weekdays"],
                          value["plan"]["time"]), ("weekly", [1, 2, 3, 4, 5], "08:00"))
        self.assertIsNone(value["wake_at"])

    def test_plan_without_a_battery_is_unsupported(self):
        self.write_plan(make_plan(Once(NOW + 3600), NOW + 3600, 100, 80, 90, READINESS_FIRST,
                                  NOW))
        self.assertEqual(self.status()["phase"], "unsupported")

    def test_corrupt_plan_is_reported_but_never_backed_up(self):
        self.plan_path.parent.mkdir(parents=True)
        self.plan_path.write_bytes(b"{broken")
        value = self.status()
        self.assertEqual((value["plan_file"], value["plan"]), ("invalid", None))
        self.assertEqual(self.files(), ["data/" + SmartCharging.PLAN_FILE])
        self.plan_path.write_text(json.dumps({"format": 9, "plan": None}))
        self.assertEqual(self.status()["plan_file"], "newer")

    def test_last_twenty_log_lines(self):
        self.log_path.parent.mkdir(parents=True)
        self.log_path.write_text("".join(f"2027-01-15T16:00:00+0800 第 {index} 筆\n"
                                         for index in range(30)))
        log = self.status()["log"]
        self.assertEqual(len(log), 20)
        self.assertTrue(log[-1].endswith("第 29 筆"))

    def test_text_summary(self):
        sysfs.thinkpad(self.root, online=1, status="Not charging", capacity=80)
        deadline = NOW + 12 * 3600
        self.write_plan(make_plan(Once(deadline), deadline, 100, 80, 90, PRECISE_WAKE, NOW))
        lines = plan_status.status_text(self.status())
        self.assertIn("等待開始", lines[0])
        self.assertTrue(any("01:56" in line for line in lines))


class Command(Base):
    def test_cli_prints_json_without_loading_gtk(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base),
               "XDG_DATA_HOME": str(self.base/"data-home"),
               "XDG_STATE_HOME": str(self.base/"state-home"),
               "XDG_CONFIG_HOME": str(self.base/"config-home")}
        code = ("import sys, runpy; sys.argv = ['device_monitor.py', '--charging-plan-status'];"
                "result = runpy.run_path('device_monitor.py', run_name='not_main');"
                "status = result['main'](sys.argv);"
                "print('GI' if 'gi' in sys.modules else 'NO-GI')")
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[-1], "NO-GI")
        value = json.loads("\n".join(lines[:-1]))
        self.assertIn("phase", value)
        self.assertFalse((self.base/"data-home").exists() or (self.base/"state-home").exists())

    def test_cannot_be_combined(self):
        result = subprocess.run([sys.executable, "-B", "device_monitor.py",
                                 "--charging-plan-status", "--preview", "laptop-battery"],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
