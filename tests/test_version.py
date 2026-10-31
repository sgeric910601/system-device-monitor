"""版本號（VERSION）：.deb 的版本、一鍵更新的版本比較、移除腳本與文件都用同一個版本。

改版時除了 VERSION，也要更新 CHANGELOG.md 的第一則，以及 README.md 與 docs/BUILD.md 裡的
套件檔名；這些測試確認它們跟得上，也確認 VERSION 符合 Debian 版本格式與 Release 的檔名，
而且 prerm／postrm 把它當成有智慧充電的版本。
"""
import re
import shutil
import subprocess
import unittest

from sdm.core import update_logic
from sdm.core.update_logic import AppVersion
from sdm.paths import VERSION
from tests.fixtures.helpers import ROOT, load_script

build = load_script("build_deb")
PACKAGE_FILE = f"system-device-monitor_{VERSION}_all.deb"
# 更新紀錄每一版的標題：「## 0.3.0（2026-10-30）」；0.2.0 以前的舊標題與測試版
# （「## 0.4.0~beta.1」，正式發布時才寫日期）可以沒有日期
HEADING = re.compile(r"^## (\S+?)(?:（(\d{4}-\d{2}-\d{2})）)?$", re.M)
BETA = "~beta."
SINCE = re.compile(r'^SMART_CHARGING_SINCE="([^"]+)"$', re.M)


def read(name):
    return (ROOT/name).read_text(encoding="utf-8")


def changelog_entries(text=None):
    """{版本: (日期或 None, 內文)}，依檔案中的順序；沒有指定文字時讀 CHANGELOG.md。"""
    text = read("CHANGELOG.md") if text is None else text
    headings = list(HEADING.finditer(text))
    ends = [match.start() for match in headings[1:]] + [len(text)]
    return {match.group(1): (match.group(2), text[match.end():end])
            for match, end in zip(headings, ends)}


def newest_entry_problem(entries, version):
    """第一則必須是這個版本；正式版要寫日期，測試版可以不寫。沒有問題時回傳 None。"""
    if not entries:
        return "the changelog has no entries"
    newest = next(iter(entries))
    if newest != version:
        return f"the newest entry is {newest}, not {version}"
    if entries[version][0] is None and BETA not in version:
        return f"the {version} entry has no date"
    return None


class Scheme(unittest.TestCase):
    def test_version_is_a_three_part_release_or_beta(self):
        """一鍵更新只認「主.次.修訂」或「主.次.修訂~beta.N」，並以同一個檔名在 Release 裡找 .deb。"""
        current = AppVersion.current(VERSION)
        self.assertEqual(update_logic.debian_version(current), VERSION)
        self.assertEqual(update_logic.asset_name(current), PACKAGE_FILE)

    def test_package_and_launcher_use_this_version(self):
        self.assertEqual(build.VERSION, VERSION)
        self.assertIn(f"\nVersion: {VERSION}\n", build.control_text(build.VERSION))

    def test_released_versions_are_older(self):
        for older in ("0.1.0", "0.2.0"):
            with self.subTest(older=older):
                self.assertLess(AppVersion.current(older), AppVersion.current(VERSION))


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class SmartChargingUpgrade(unittest.TestCase):
    """prerm／postrm 只在降級到比 SMART_CHARGING_SINCE 舊的版本時撤銷無人授權的規則；
    升級到這個版本（或重新安裝同一版）時必須保留。"""

    def since(self, script):
        return SINCE.search(read("packaging/debian/" + script)).group(1)

    def at_least(self, version, since):
        return subprocess.run(["dpkg", "--compare-versions", version, "ge", since]).returncode == 0

    def test_this_version_and_its_betas_count_as_having_smart_charging(self):
        base = VERSION.split("~", 1)[0]
        for script in ("prerm", "postrm"):
            since = self.since(script)
            for version in (VERSION, base, base + "~beta.1"):
                with self.subTest(script=script, version=version):
                    self.assertTrue(self.at_least(version, since))
            with self.subTest(script=script, version="0.2.0"):
                self.assertFalse(self.at_least("0.2.0", since))


class ChangelogRule(unittest.TestCase):
    """第一則必須是 VERSION；正式版寫日期，測試版（~beta.N）可以先不寫。"""

    def problem(self, text, version):
        return newest_entry_problem(changelog_entries(text), version)

    def test_dated_release_and_undated_beta_are_accepted(self):
        self.assertIsNone(self.problem("# x\n\n## 0.3.0（2026-10-30）\n\n- a\n", "0.3.0"))
        self.assertIsNone(self.problem("# x\n\n## 0.4.0~beta.1\n\n- b\n\n## 0.3.0（2026-10-30）\n",
                                       "0.4.0~beta.1"))
        self.assertIsNone(self.problem("# x\n\n## 0.4.0~beta.1（2026-11-02）\n", "0.4.0~beta.1"))

    def test_undated_release_wrong_top_and_empty_are_rejected(self):
        self.assertIsNotNone(self.problem("# x\n\n## 0.4.0\n", "0.4.0"))
        self.assertIsNotNone(self.problem("# x\n\n## 未發布\n\n## 0.3.0（2026-10-30）\n", "0.3.0"))
        self.assertIsNotNone(self.problem("# x\n\n## 0.3.0（2026-10-30）\n", "0.4.0~beta.1"))
        self.assertIsNotNone(self.problem("# x\n", "0.3.0"))


class Documents(unittest.TestCase):
    def test_changelog_starts_with_an_entry_for_this_version(self):
        self.assertIsNone(newest_entry_problem(changelog_entries(), VERSION))

    def test_changelog_keeps_every_release(self):
        self.assertTrue({"0.1.0", "0.2.0", VERSION} <= set(changelog_entries()))

    def test_install_instructions_name_this_version(self):
        for name in ("README.md", "docs/BUILD.md"):
            with self.subTest(document=name):
                text = read(name)
                self.assertTrue(PACKAGE_FILE in text, f"{name} does not name {PACKAGE_FILE}")
                self.assertEqual(set(re.findall(r"system-device-monitor_([0-9][^_\s]*)_all\.deb",
                                                text)), {VERSION})


if __name__ == "__main__":
    unittest.main()
