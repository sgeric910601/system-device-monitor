"""使用者文件：新使用者照著 README 能安裝、使用與移除；文件之間的連結與錨點都存在；
中文用全形標點；刻意不做的功能、筆電功能只以合成資料驗證、一鍵更新尚未啟用都寫清楚。"""
import re
import unicodedata
import unittest

from sdm.constants import Update
from tests.fixtures.helpers import ROOT, load_script

build = load_script("build_deb")
DOCUMENTS = ("README.md", "CHANGELOG.md", "docs/USAGE.md", "docs/PRIVACY.md", "docs/BUILD.md",
             "docs/SECURITY.md")
MAX_LINES = 800
LINK = re.compile(r"\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6} (.+?)\s*$", re.M)
CODE_SPAN = re.compile(r"`[^`]*`")
CJK = "[㐀-鿿　-〿＀-￯]"
HALF_WIDTH = re.compile(rf"(?<={CJK})[,:;()]|[,:;()](?={CJK})")
EXTERNAL = ("http://", "https://", "mailto:")
# README 必須有的章節（依新使用者的順序）
README_SECTIONS = ("系統需求", "安裝", "選用的管理員元件", "使用", "更新", "移除", "刻意不做的功能")
COMMANDS = ("--background", "--version", "--preview", "--dump", "--charging-plan-status",
            "--test-notification")
HELPERS = ("sdm-cpu-power", "sdm-charge-limit", "sdm-wake", "sdm-unattended", "sdm-update")
# 一鍵更新還沒有內建發布公鑰時，README 與使用說明都要寫這句；設定了公鑰之後必須拿掉
NO_RELEASE_KEY = "還沒有內建發布公鑰"


def read(name):
    return (ROOT/name).read_text(encoding="utf-8")


def slug(heading):
    """GitHub 的標題錨點：小寫、去掉標點與符號（保留 - 與 _）、空白換成 -。"""
    text = heading.replace("`", "").strip().lower()
    kept = "".join(char for char in text
                   if char in "-_ " or unicodedata.category(char)[0] in "LNM")
    return kept.replace(" ", "-")


def anchors(text):
    seen, found = {}, set()
    for match in HEADING.finditer(text):
        base = slug(match.group(1))
        count = seen.get(base, 0)
        found.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return found


def prose_lines(text):
    """不含程式碼區塊的行：縮排 4 格以上、而且接在空行或程式碼後面的行是程式碼。"""
    previous_blank, in_code = True, False
    for number, line in enumerate(text.splitlines(), 1):
        blank = not line.strip()
        indented = len(line) - len(line.lstrip(" ")) >= 4
        in_code = not blank and indented and (previous_blank or in_code)
        if not blank and not in_code:
            yield number, line
        if not blank and not indented:
            in_code = False
        previous_blank = blank


def sections(text):
    """{二級標題: 內文}。"""
    parts = re.split(r"^## (.+?)\s*$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


class Structure(unittest.TestCase):
    def test_documents_stay_short(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertLess(len(read(name).splitlines()), MAX_LINES)

    def test_relative_links_and_anchors_exist(self):
        for name in DOCUMENTS:
            source = ROOT/name
            for target in LINK.findall(read(name)):
                if target.startswith(EXTERNAL):
                    continue
                path, _, anchor = target.partition("#")
                linked = (source.parent/path).resolve() if path else source
                with self.subTest(document=name, link=target):
                    self.assertTrue(linked.is_file(), f"missing file {path}")
                    if anchor:
                        self.assertIn(anchor, anchors(linked.read_text(encoding="utf-8")))

    def test_chinese_text_uses_full_width_punctuation(self):
        for name in DOCUMENTS:
            for number, line in prose_lines(read(name)):
                text = LINK.sub("]", CODE_SPAN.sub("", line))
                with self.subTest(document=name, line=number):
                    self.assertIsNone(HALF_WIDTH.search(text), line)


class Readme(unittest.TestCase):
    def setUp(self):
        self.text = read("README.md")
        self.sections = sections(self.text)

    def test_sections_follow_install_use_remove(self):
        titles = list(self.sections)
        positions = [next((index for index, title in enumerate(titles) if title.startswith(want)),
                          None) for want in README_SECTIONS]
        self.assertNotIn(None, positions, titles)
        self.assertEqual(positions, sorted(positions), titles)

    def test_requirements_list_every_package_dependency(self):
        requirements = self.sections["系統需求"]
        for dependency in build.DEPENDS:
            package = dependency.split(" ", 1)[0]
            with self.subTest(package=package):
                self.assertIn(package, requirements)
        self.assertIn("gnome-shell-extension-appindicator", requirements)

    def test_both_install_methods_and_how_to_switch(self):
        install = next(body for title, body in self.sections.items() if title.startswith("安裝"))
        for words in ("sudo apt install ./", "./install-dependencies.sh", "/usr/bin/python3 install.py",
                      "擇一", "install.py --uninstall", "sudo apt remove system-device-monitor",
                      "install.py --system-components remove"):
            with self.subTest(words=words):
                self.assertIn(words, install)

    def test_admin_components_say_what_each_does_and_how_to_remove_it(self):
        admin = self.sections["選用的管理員元件"]
        for words in ("CPU 功耗取樣", "充電上限", "精準喚醒", "允許無人時自動調整", "一鍵更新",
                      "install.py --system-components install",
                      "install.py --system-components remove", "密碼"):
            with self.subTest(words=words):
                self.assertIn(words, admin)

    def test_command_line_options(self):
        usage = self.sections["使用"]
        for option in COMMANDS:
            with self.subTest(option=option):
                self.assertIn("system-device-monitor " + option, usage)

    def test_removal_for_both_install_methods(self):
        remove = self.sections["移除"]
        for words in ("sudo apt remove system-device-monitor", "sudo apt purge system-device-monitor",
                      "install.py --system-components remove", "install.py --uninstall"):
            with self.subTest(words=words):
                self.assertIn(words, remove)

    def test_features_left_out_on_purpose(self):
        skipped = self.sections["刻意不做的功能"]
        for words in ("觸控板", "GNOME", "DMG", ".deb", "電池歷史", "GPU 耗電", "每個程序",
                      "耳機", "充電盒", "一個電量"):
            with self.subTest(words=words):
                self.assertIn(words, skipped)

    def test_laptop_features_are_marked_as_unverified(self):
        for name in ("README.md", "docs/USAGE.md"):
            with self.subTest(document=name):
                text = read(name)
                self.assertIn("合成", text)
                self.assertIn("尚未在真正的筆電上測試", text)
        notice = self.text.split("\n## ", 1)[0]
        self.assertIn("尚未在真正的筆電上測試", notice)


class Usage(unittest.TestCase):
    def setUp(self):
        self.sections = sections(read("docs/USAGE.md"))

    def test_admin_components_have_one_overview(self):
        admin = self.sections["管理員元件"]
        for words in HELPERS + ("install.py --system-components install",
                                "install.py --system-components remove", "pkexec"):
            with self.subTest(words=words):
                self.assertIn(words, admin)

    def test_command_line_options_are_listed(self):
        options = self.sections["命令列選項"]
        for option in COMMANDS + ("--smoke-test",):
            with self.subTest(option=option):
                self.assertIn(option, options)

    def test_file_locations(self):
        files = self.sections["檔案位置"]
        for name in ("settings.json", "preferences.json", "history.json", "charging-plan.json",
                     "charging-plan.log", "app.log", "autostart"):
            with self.subTest(name=name):
                self.assertIn(name, files)


class ReleaseKey(unittest.TestCase):
    def test_docs_say_whether_one_click_updates_work(self):
        """沒有發布公鑰時一鍵更新不能使用：README 與使用說明都要說，也要說使用者會看到什麼。"""
        missing = not Update.PUBLIC_KEYS
        for name in ("README.md", "docs/USAGE.md"):
            with self.subTest(document=name):
                text = read(name)
                self.assertEqual(NO_RELEASE_KEY in text, missing)
                if missing:
                    self.assertIn("未設定簽章金鑰", text)


if __name__ == "__main__":
    unittest.main()
