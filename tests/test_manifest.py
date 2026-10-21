"""package-files.json 一致性：公開來源、執行期檔案與 git 追蹤檔案必須相符。"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Manifest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT/"package-files.json").read_text())
        self.source = set(self.manifest["source"])
        self.runtime = self.manifest["runtime"]

    def test_format_two_with_all_sections(self):
        self.assertEqual(self.manifest["format"], 2)
        self.assertIsInstance(self.manifest["system"], list)
        self.assertIsInstance(self.manifest["maintainer"], dict)

    def test_every_tracked_file_is_a_public_source(self):
        if not (ROOT/".git").exists() or shutil.which("git") is None:
            self.skipTest("not a git checkout")
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=True)
        missing = sorted(name for name in tracked.split("\0") if name and name not in self.source)
        self.assertEqual(missing, [])

    def test_every_package_module_is_shipped(self):
        modules = {str(path.relative_to(ROOT)) for path in (ROOT/"sdm").rglob("*.py")}
        self.assertEqual(sorted(modules - set(self.runtime)), [])
        self.assertIn("device_monitor.py", self.runtime)

    def test_runtime_files_are_public_and_exist(self):
        self.assertEqual(sorted(set(self.runtime) - self.source), [])
        self.assertEqual([name for name in self.runtime if not (ROOT/name).is_file()], [])
        self.assertEqual(len(self.runtime), len(set(self.runtime)))

    def test_test_package_marker_exists(self):
        self.assertTrue((ROOT/"tests/__init__.py").is_file())
        self.assertIn("tests/__init__.py", self.source)

    def test_real_manifest_passes_build_validation(self):
        build = load_script("build_deb")
        plan = build.package_plan(self.manifest, ROOT)
        self.assertIn(Path("usr/share/system-device-monitor/sdm/paths.py"),
                      [Path(entry.target) for entry in plan])


class PrivacyManifest(unittest.TestCase):
    def setUp(self):
        self.privacy = load_script("privacy_check")
        self.manifest = {"format": 2, "runtime": ["a.py"], "system": [], "maintainer": {},
                         "source": ["a.py", "helper/tool", "packaging/postinst"]}

    def test_format_two_is_accepted(self):
        self.assertEqual(self.privacy.manifest_errors(self.manifest), [])

    def test_packaged_sources_must_be_public(self):
        manifest = dict(self.manifest, source=["a.py"],
                        system=[{"source": "helper/tool", "target": "/usr/lib/x/tool"}],
                        maintainer={"postinst": "packaging/postinst"})
        self.assertEqual(sorted(name for name, _ in self.privacy.manifest_errors(manifest)),
                         ["helper/tool", "packaging/postinst"])

    def test_runtime_outside_source_and_unknown_format_are_reported(self):
        manifest = dict(self.manifest, format=1, runtime=["a.py", "b.py"])
        self.assertEqual(len(self.privacy.manifest_errors(manifest)), 2)


if __name__ == "__main__":
    unittest.main()
