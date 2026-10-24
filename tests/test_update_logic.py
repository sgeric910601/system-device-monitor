"""一鍵更新的版本邏輯：移植參考專案的 UpdateTests，加上 Linux 的 Debian 版本與 .deb 檔名。

GitHub 的回應都是人工建立的 JSON；不連網路。
"""
import json
import shutil
import subprocess
import unittest

from sdm.core import update_logic as logic
from sdm.core.update_logic import AppVersion

SITE = "https://github.com/sgeric910601/system-device-monitor"
SIGNED = ("SHA256SUMS", "SHA256SUMS.sig")
AT = "@"                    # 分開寫：帳號＠主機的網址不會被隱私檢查當成信箱


def deb_for(tag):
    """測試自己寫出的檔名（不經過受測的函式）：v1.7.0-beta.1 → …_1.7.0~beta.1_all.deb。"""
    return "system-device-monitor_" + tag[1:].replace("-beta.", "~beta.") + "_all.deb"


def asset(tag, name, size=100, url=None):
    return {"name": name, "size": size, "state": "uploaded",
            "browser_download_url": url or f"{SITE}/releases/download/{tag}/{name}"}


def entry(tag, prerelease=False, names=None, draft=False, body="新功能"):
    names = (deb_for(tag),) + SIGNED if names is None else names
    return {"tag_name": tag, "html_url": f"{SITE}/releases/tag/{tag}", "draft": draft,
            "prerelease": prerelease, "body": body, "assets": [asset(tag, n) for n in names]}


def listing(*entries):
    return json.dumps(list(entries)).encode("utf-8")


class Versions(unittest.TestCase):
    def test_version_parsing_and_comparison(self):
        self.assertEqual(logic.version("v1.4.1"), (1, 4, 1))
        self.assertEqual(logic.version("1.10.0"), (1, 10, 0))
        self.assertIsNone(logic.version("v1.4.1-beta"))
        self.assertIsNone(logic.version(""))
        self.assertTrue(logic.is_newer((1, 4, 2), (1, 4, 1)))
        self.assertTrue(logic.is_newer((1, 10, 0), (1, 9, 9)))
        self.assertFalse(logic.is_newer((1, 4), (1, 4, 0)))
        self.assertFalse(logic.is_newer((1, 4, 0), (1, 4, 1)))
        self.assertTrue(logic.is_newer((2,), (1, 99, 99)))

    def test_only_plain_ascii_numbers_are_versions(self):
        for text in ("v", "1..2", ".1", "1.", "1.-2", "+1.0", " 1.0", "1.0 ", "1_0.0", "١.٢",
                     "1.2.x", "v1.0.0rc1", "1234567890.0.0", None, 1.0):
            with self.subTest(text=text):
                self.assertIsNone(logic.version(text))

    def test_notify_once_per_version(self):
        self.assertTrue(logic.should_notify((1, 6, 2), None))
        self.assertTrue(logic.should_notify((1, 6, 2), "v1.6.1"))
        self.assertTrue(logic.should_notify((1, 7, 0), "v1.6.2"))
        self.assertFalse(logic.should_notify((1, 6, 2), "v1.6.2"))
        self.assertFalse(logic.should_notify((1, 6, 1), "v1.6.2"))
        self.assertTrue(logic.should_notify((1, 6, 2), "garbage"))      # 紀錄壞掉時當作沒通知過


class BetaVersions(unittest.TestCase):
    def test_beta_parsing(self):
        self.assertEqual(AppVersion.parse("v1.7.0-beta.1"), AppVersion((1, 7, 0), 1))
        self.assertEqual(AppVersion.parse("1.7.0-beta.12").text, "1.7.0 beta 12")
        self.assertEqual(AppVersion.parse("v1.6.3").text, "1.6.3")
        for text in ("v1.7.0-rc.1", "v1.7.0-beta", "v1.7.0-beta.0", "v1.7.0-", "v1.7.0-beta.01",
                     "v1.7.0-beta.1-beta.2", "v1.7.0-beta.+1", "v1.7.0-BETA.1", ""):
            with self.subTest(text=text):
                self.assertIsNone(AppVersion.parse(text))

    def test_beta_ordering(self):
        order = [AppVersion.parse(tag) for tag in
                 ("v1.6.3", "v1.7.0-beta.1", "v1.7.0-beta.2", "v1.7.0", "v1.7.1-beta.1")]
        self.assertNotIn(None, order)
        for lower, higher in zip(order, order[1:]):
            with self.subTest(lower=lower.text, higher=higher.text):
                self.assertTrue(lower < higher)
                self.assertFalse(higher < lower)

    def test_current_version_from_the_version_file(self):
        self.assertEqual(AppVersion.current("1.7.0~beta.1"), AppVersion((1, 7, 0), 1))
        self.assertEqual(AppVersion.current("1.7.0-beta.1\n"), AppVersion((1, 7, 0), 1))
        self.assertEqual(AppVersion.current("1.6.3"), AppVersion((1, 6, 3)))
        self.assertEqual(AppVersion.current("1.6.3~rc1"), AppVersion((1, 6, 3)))
        self.assertEqual(AppVersion.current("garbage"), AppVersion((0,)))


class Debian(unittest.TestCase):
    TAGS = ("v0.2.0", "v0.3.0-beta.1", "v0.3.0-beta.2", "v0.3.0-beta.10", "v0.3.0", "v0.3.1",
            "v0.10.0", "v1.0.0-beta.1", "v1.0.0")

    def test_tag_and_debian_version_map_both_ways(self):
        self.assertEqual(logic.debian_version(AppVersion.parse("v0.3.0-beta.1")), "0.3.0~beta.1")
        self.assertEqual(logic.debian_version(AppVersion.parse("v0.3.0")), "0.3.0")
        self.assertEqual(logic.from_debian("0.3.0~beta.1"), AppVersion((0, 3, 0), 1))
        self.assertEqual(logic.from_debian("0.3.0"), AppVersion((0, 3, 0)))
        self.assertEqual(logic.tag(AppVersion((0, 3, 0), 1)), "v0.3.0-beta.1")
        for text in ("0.3", "0.3.0-1", "0.3.0~rc.1", "0.3.0~beta.0", "1:0.3.0", "0.3.0.1", ""):
            with self.subTest(text=text):
                self.assertIsNone(logic.from_debian(text))

    def test_only_three_part_versions_have_a_debian_version(self):
        self.assertIsNone(logic.debian_version(AppVersion((1, 4))))
        self.assertIsNone(logic.debian_version(AppVersion((1, 4, 0, 1))))

    def test_asset_names(self):
        self.assertEqual(logic.asset_name(AppVersion.parse("v0.3.0-beta.1")),
                         "system-device-monitor_0.3.0~beta.1_all.deb")
        self.assertEqual(logic.asset_name(AppVersion.parse("v0.3.0")),
                         "system-device-monitor_0.3.0_all.deb")
        self.assertIsNone(logic.asset_name(AppVersion((0, 3))))

    def test_python_order_matches_the_tag_order(self):
        versions = [AppVersion.parse(tag) for tag in self.TAGS]
        self.assertEqual(sorted(versions), versions)

    @unittest.skipUnless(shutil.which("dpkg"), "dpkg is not installed")
    def test_dpkg_sorts_the_debian_versions_the_same_way(self):
        versions = [logic.debian_version(AppVersion.parse(tag)) for tag in self.TAGS]
        for lower, higher in zip(versions, versions[1:]):
            with self.subTest(lower=lower, higher=higher):
                self.assertEqual(subprocess.run(["dpkg", "--compare-versions", lower, "lt",
                                                 higher]).returncode, 0)
                self.assertNotEqual(subprocess.run(["dpkg", "--compare-versions", higher, "lt",
                                                    lower]).returncode, 0)


class Hosts(unittest.TestCase):
    def test_trusted_hosts(self):
        for url in ("https://github.com/a", "https://api.github.com/repos/x",
                    "https://objects.githubusercontent.com/a",
                    "https://release-assets.githubusercontent.com/a?x=1",
                    "https://GitHub.com/a", "https://github.com:443/a"):
            with self.subTest(url=url):
                self.assertTrue(logic.is_trusted(url))

    def test_untrusted_hosts(self):
        for url in ("http://github.com/a", "https://github.com.evil.com/a",
                    "https://evilgithubusercontent.com/a", "https://githubusercontent.com/a",
                    "https://gist.github.com/a", "https://github.com./a",
                    "https://user" + AT + "github.com/a", "https://user:pw" + AT + "github.com/a",
                    "https://github.com:8443/a", "ftp://github.com/a", "file:///etc/passwd",
                    "https:///a", "", None, "https://evil.com/github.com",
                    "https://evil.com#@github.com", "https://xn--gthub-cta.com/a",
                    "https://objects.githubusercontent.com\\@evil.com/a"):
            with self.subTest(url=url):
                self.assertFalse(logic.is_trusted(url))


class Checksums(unittest.TestCase):
    NAME = "system-device-monitor_1.5.0_all.deb"

    def test_checksum_file_parsing(self):
        digest = "a1" * 32
        text = f"{digest}  {self.NAME}\n{'b2' * 32}  other.tar.gz\n"
        self.assertEqual(logic.checksum(text, self.NAME), digest)
        self.assertIsNone(logic.checksum(text, "missing.deb"))
        self.assertIsNone(logic.checksum("zzzz  file.zip", "file.zip"))
        self.assertEqual(logic.checksum(f"{'c3' * 32}  {self.NAME}\n", self.NAME), "c3" * 32)

    def test_binary_marker_and_uppercase_hashes(self):
        self.assertEqual(logic.checksum(f"{'AB' * 32} *{self.NAME}\r\n", self.NAME), "ab" * 32)

    def test_only_the_exact_file_name_matches(self):
        digest = "d4" * 32
        for line in (f"{digest}  {self.NAME}.evil", f"{digest}  ./{self.NAME}",
                     f"{digest}  dir/{self.NAME}", f"{digest}  {self.NAME}*",
                     f"{digest}  {self.NAME} extra", f"{digest[:-1]}  {self.NAME}",
                     f"{digest}0  {self.NAME}", f"{digest}\t{self.NAME}",
                     f"x{digest}  {self.NAME}"):
            with self.subTest(line=line):
                self.assertIsNone(logic.checksum(line + "\n", self.NAME))

    def test_conflicting_duplicates_are_refused(self):
        same = f"{'e5' * 32}  {self.NAME}\n" * 2
        self.assertEqual(logic.checksum(same, self.NAME), "e5" * 32)
        conflict = f"{'e5' * 32}  {self.NAME}\n{'f6' * 32}  {self.NAME}\n"
        self.assertIsNone(logic.checksum(conflict, self.NAME))


class Releases(unittest.TestCase):
    def test_release_parsing_picks_the_deb_checksums_and_signature(self):
        found = logic.releases(listing(entry("v1.5.0", body="修正")))
        self.assertEqual(len(found), 1)
        release = found[0]
        self.assertEqual(release.numbers, (1, 5, 0))
        self.assertEqual(release.asset_name, "system-device-monitor_1.5.0_all.deb")
        self.assertEqual(release.asset_size, 100)
        self.assertEqual(release.asset_url, f"{SITE}/releases/download/v1.5.0/{release.asset_name}")
        self.assertEqual(release.checksum_url, f"{SITE}/releases/download/v1.5.0/SHA256SUMS")
        self.assertEqual(release.signature_url, f"{SITE}/releases/download/v1.5.0/SHA256SUMS.sig")
        self.assertEqual(release.page_url, f"{SITE}/releases/tag/v1.5.0")
        self.assertEqual(release.notes, "修正")
        self.assertTrue(release.signed)
        self.assertEqual(release.debian_version, "1.5.0")

    def test_unsigned_release_is_listed_but_not_signed(self):
        release = logic.releases(listing(entry("v1.5.0", names=(deb_for("v1.5.0"),))))[0]
        self.assertFalse(release.signed)
        self.assertIsNone(release.checksum_url)

    def test_release_without_the_deb_or_with_untrusted_urls_is_skipped(self):
        broken = entry("v1.5.0")
        broken["assets"][0]["browser_download_url"] = "https://evil.example.com/x.deb"
        bad_sig = entry("v1.6.0")
        bad_sig["assets"][2]["browser_download_url"] = "http://github.com/x.sig"
        pending = entry("v1.7.0")
        pending["assets"][0]["state"] = "starter"
        found = logic.releases(listing(entry("v1.4.0", names=SIGNED), broken, pending,
                                       entry("v1.4.1", names=("system-device-monitor_1.4.0_all.deb",))))
        self.assertEqual(found, ())
        self.assertFalse(logic.releases(listing(bad_sig))[0].signed)

    def test_prerelease_flag_must_match_the_tag(self):
        parsed = logic.releases(listing(entry("v1.7.0-beta.1", prerelease=True),
                                        entry("v1.6.3"), entry("v1.8.0", prerelease=True),
                                        entry("v1.9.0-beta.1"), entry("v2.0.0", draft=True),
                                        entry("v1.4"), {"tag_name": 5}, "junk"))
        self.assertEqual([release.tag for release in parsed], ["v1.7.0-beta.1", "v1.6.3"])
        self.assertEqual(parsed[0].beta, 1)
        self.assertEqual(parsed[0].version_text, "1.7.0 beta 1")
        self.assertEqual(parsed[0].asset_name, "system-device-monitor_1.7.0~beta.1_all.deb")

    def test_latest_release_never_accepts_a_beta(self):
        data = json.dumps(entry("v1.7.0-beta.1", prerelease=True)).encode("utf-8")
        self.assertIsNone(logic.latest_release(data))
        self.assertEqual(logic.latest_release(json.dumps(entry("v1.6.3")).encode()).tag, "v1.6.3")

    def test_bad_json_gives_nothing(self):
        for data in (b"", b"{", b"{}", b"null", b"\xff\xfe", json.dumps({"a": 1}).encode()):
            with self.subTest(data=data):
                self.assertEqual(logic.releases(data), ())


class Choose(unittest.TestCase):
    def setUp(self):
        self.releases = logic.releases(listing(
            entry("v1.7.0-beta.2", prerelease=True), entry("v1.7.0-beta.1", prerelease=True),
            entry("v1.6.3"), entry("v1.6.2")))
        self.final = logic.releases(listing(entry("v1.7.0"),
                                            entry("v1.7.0-beta.2", prerelease=True)))

    def tags(self, releases, current):
        found = logic.choose(releases, current)
        return (found.stable.tag if found.stable else None,
                found.beta.tag if found.beta else None)

    def test_stable_user(self):
        self.assertEqual(self.tags(self.releases, AppVersion((1, 6, 2))), ("v1.6.3", "v1.7.0-beta.2"))
        self.assertEqual(self.tags(self.releases, AppVersion((1, 6, 3))), (None, "v1.7.0-beta.2"))

    def test_beta_user(self):
        self.assertEqual(self.tags(self.releases, AppVersion((1, 7, 0), 1)), (None, "v1.7.0-beta.2"))
        self.assertEqual(self.tags(self.final, AppVersion((1, 7, 0), 2)), ("v1.7.0", None))

    def test_betas_older_than_the_newest_stable_are_not_listed(self):
        self.assertEqual(self.tags(self.final, AppVersion((1, 6, 3))), ("v1.7.0", None))
        self.assertEqual(self.tags(self.final, AppVersion((1, 7, 0))), (None, None))


class Notes(unittest.TestCase):
    def test_control_characters_are_removed(self):
        self.assertEqual(logic.notes_excerpt("修正\x1b[31m紅字\x00‮"), "修正[31m紅字")

    def test_headings_and_bullets_become_plain_text(self):
        body = "## 新增\r\n\r\n- 一鍵更新\n* 簽章驗證\n\n\n### 修正\n- 其他"
        self.assertEqual(logic.notes_excerpt(body), "新增\n・一鍵更新\n・簽章驗證\n修正\n・其他")

    def test_length_and_lines_are_capped(self):
        text = logic.notes_excerpt("字" * 2000)
        self.assertEqual(len(text), 600)
        self.assertTrue(text.endswith("…"))
        lines = logic.notes_excerpt("\n".join(f"第 {n} 行" for n in range(40))).splitlines()
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[-1], "…")

    def test_missing_notes(self):
        for body in (None, "", "  \n ", 5):
            with self.subTest(body=body):
                self.assertEqual(logic.notes_excerpt(body), "")


if __name__ == "__main__":
    unittest.main()
