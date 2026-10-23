"""程式圖示換色：只寫入、只刪除本程式自己建立（有記錄）的圖示檔，全部在暫存的 XDG 資料目錄。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sdm.services import app_icon
from sdm.services.autostart import desktop_entry

ROOT = Path(__file__).resolve().parents[1]
HICOLOR = "icons/hicolor"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Overrides(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)/"share"
        self.data = self.home/"system-device-monitor"
        self.targets = [self.home/HICOLOR/f"{size}x{size}/apps/system-device-monitor.png"
                        for size in (128, 512)]

    def apply(self, color, packaged=True, keep_default=False):
        return app_icon.apply_color(color, packaged=packaged, data_home=self.home,
                                    data_dir=self.data, root=ROOT, keep_default=keep_default)

    def asset(self, color, size):
        return (ROOT/"assets/app"/f"{color}-{size}.png").read_bytes()

    def test_targets_are_the_hicolor_app_icons(self):
        self.assertEqual(list(app_icon.theme_targets(self.home).values()), self.targets)

    def test_other_colour_writes_both_sizes_and_records_them(self):
        result = self.apply("peach")
        self.assertEqual(result, app_icon.IconResult(True, None))
        for target, size in zip(self.targets, (128, 512)):
            self.assertEqual(target.read_bytes(), self.asset("peach", size))
        marker = json.loads((self.data/"app-icon.json").read_text())
        self.assertEqual(marker["files"], {str(target): digest(target) for target in self.targets})

    def test_same_colour_again_changes_nothing(self):
        self.apply("night")
        self.assertEqual(self.apply("night"), app_icon.IconResult(False, None))

    def test_default_on_a_package_install_removes_only_own_files(self):
        self.apply("sky")
        neighbour = self.targets[0].with_name("other-app.png")
        neighbour.write_bytes(b"someone else")
        self.assertEqual(self.apply("mint"), app_icon.IconResult(True, None))
        self.assertEqual([target.exists() for target in self.targets], [False, False])
        self.assertFalse((self.data/"app-icon.json").exists())
        self.assertEqual(neighbour.read_bytes(), b"someone else")

    def test_default_without_overrides_writes_nothing(self):
        self.assertEqual(self.apply("mint"), app_icon.IconResult(False, None))
        self.assertFalse(self.home.exists())

    def test_source_install_writes_the_default_when_asked(self):
        self.assertEqual(self.apply("mint", packaged=False), app_icon.IconResult(False, None))
        self.assertFalse(self.home.exists())
        self.apply("mint", packaged=False, keep_default=True)
        self.assertEqual(self.targets[1].read_bytes(), self.asset("mint", 512))
        self.apply("peach", packaged=False)
        self.apply("mint", packaged=False)
        self.assertEqual(self.targets[0].read_bytes(), self.asset("mint", 128))

    def test_foreign_file_is_never_overwritten(self):
        self.targets[0].parent.mkdir(parents=True)
        self.targets[0].write_bytes(b"installed by hand")
        result = self.apply("peach")
        self.assertIsNotNone(result.problem)
        self.assertEqual(self.targets[0].read_bytes(), b"installed by hand")
        self.assertEqual(self.targets[1].read_bytes(), self.asset("peach", 512))

    def test_modified_files_are_kept_when_switching_back(self):
        self.apply("peach")
        self.targets[1].write_bytes(b"edited by the user")
        self.apply("mint")
        self.assertFalse(self.targets[0].exists())
        self.assertEqual(self.targets[1].read_bytes(), b"edited by the user")

    def test_tampered_marker_cannot_delete_other_files(self):
        victim = self.home/"notes.txt"
        victim.parent.mkdir(parents=True)
        victim.write_bytes(b"keep me")
        self.data.mkdir(parents=True)
        (self.data/"app-icon.json").write_text(json.dumps(
            {"color": "peach", "files": {str(victim): digest(victim)}}))
        self.apply("mint")
        self.assertEqual(victim.read_bytes(), b"keep me")

    def test_write_failure_is_reported_not_raised(self):
        self.home.mkdir(parents=True)
        (self.home/"icons").write_bytes(b"a file where a folder should be")
        with self.assertLogs("sdm.services.app_icon", "WARNING"):
            result = self.apply("sky")
        self.assertIsNotNone(result.problem)
        self.assertFalse(result.changed)

    def test_unknown_colour_is_rejected(self):
        with self.assertRaises(ValueError):
            self.apply("purple")

    def test_remove_all_cleans_up_for_uninstall(self):
        self.apply("night", packaged=False)
        app_icon.remove_overrides(self.home, self.data)
        self.assertEqual([target.exists() for target in self.targets], [False, False])


class Assets(unittest.TestCase):
    def test_every_colour_has_both_sizes(self):
        for color in app_icon.COLORS:
            for size in (128, 512):
                with self.subTest(color=color, size=size):
                    self.assertTrue(app_icon.asset_path(color, size, ROOT).is_file())

    def test_titles(self):
        self.assertEqual([app_icon.TITLES[color] for color in app_icon.COLORS],
                         ["薄荷綠", "暖杏色", "深夜藍", "天空藍"])

    def test_package_installs_the_default_into_hicolor(self):
        manifest = json.loads((ROOT/"package-files.json").read_text())
        for size in (128, 512):
            with self.subTest(size=size):
                self.assertIn({"source": f"assets/app/mint-{size}.png",
                               "target": f"/usr/share/icons/hicolor/{size}x{size}/apps/"
                                         "system-device-monitor.png",
                               "mode": "0644"}, manifest["system"])

    def test_desktop_entries_use_the_themed_icon_name(self):
        self.assertIn("\nIcon=system-device-monitor\n", desktop_entry(launcher="/usr/bin/x"))

    def test_package_detection(self):
        self.assertTrue(app_icon.is_packaged(Path("/usr/share/system-device-monitor")))
        self.assertFalse(app_icon.is_packaged(ROOT))


if __name__ == "__main__":
    unittest.main()
