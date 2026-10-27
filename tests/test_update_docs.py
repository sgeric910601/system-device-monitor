"""一鍵更新的說明：隱私（唯一的網路存取）、簽章模型、發布流程與使用方式都寫清楚，
不再無條件宣稱「沒有任何網路存取」。"""
import unittest

from sdm.ui.settings import about
from tests.fixtures.helpers import ROOT, load_script

build = load_script("build_deb")


def read(name):
    return (ROOT/name).read_text(encoding="utf-8")


class Claims(unittest.TestCase):
    def test_package_description_mentions_the_update_check(self):
        text = build.control_text("1.2.3")
        self.assertTrue(text.endswith(" No telemetry; optional update check against GitHub.\n"))

    def test_readme_and_about_qualify_the_no_network_claim(self):
        readme = read("README.md")
        self.assertNotIn("No telemetry, accounts, or cloud service required.", readme)
        self.assertIn("optional update check", readme)
        self.assertIn("GitHub", about.PURPOSE)


class Documents(unittest.TestCase):
    def check(self, name, *phrases):
        text = read(name)
        for phrase in phrases:
            with self.subTest(document=name, phrase=phrase):
                self.assertIn(phrase, text)

    def test_privacy(self):
        self.check("docs/PRIVACY.md", "api.github.com", "githubusercontent.com", "User-Agent",
                   "自動檢查更新", "24 小時", "update-state.json")

    def test_security(self):
        self.check("docs/SECURITY.md", "sdm-update", "tw.local.system-device-monitor.update",
                   "SHA256SUMS.sig", "Update.PUBLIC_KEYS", "離線", "新舊並存", "Sparkle",
                   "/var/cache/system-device-monitor/staging", "apt-get", "--no-remove",
                   "dpkg-deb -c")

    def test_security_states_the_limits_of_signatures(self):
        self.check("docs/SECURITY.md", "隱藏比較新的版本", "比已安裝的新、但不是最新",
                   "簽章不含發布管道或時間", "--allow-beta", "本專案專用",
                   "絕不沿用 macOS 版（Sparkle）的金鑰", "只是縱深防禦，把關的是簽章")

    def test_key_generation_warns_against_reusing_keys(self):
        keys = load_script("release_keys")
        self.assertIn("本專案專用", keys.BACKUP)
        self.assertIn("Sparkle", keys.BACKUP)
        self.check("docs/BUILD.md", "本專案專用")

    def test_helper_marks_the_allowlist_as_defence_in_depth(self):
        self.check("helper/sdm-update", "只是縱深防禦，把關的是簽章")

    def test_build(self):
        self.check("docs/BUILD.md", "release_keys.py generate --out-dir", "--signing-key",
                   "SDM_RELEASE_SIGNING_KEY", "SHA256SUMS.sig", "make release-assets SIGNING_KEY=")

    def test_usage(self):
        self.check("docs/USAGE.md", "設定 → 更新", "git pull --ff-only", "立即檢查", "安裝更新")

    def test_changelog(self):
        unreleased = read("CHANGELOG.md").split("\n## ", 2)[1]
        self.assertTrue(unreleased.startswith("未發布"))
        self.assertIn("一鍵更新", unreleased)


if __name__ == "__main__":
    unittest.main()
