"""打包腳本測試：以人工建立的清單暫存套件內容，檢查權限、路徑限制與控制檔。"""
import ast
import copy
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "CHANGELOG.md", "docs/USAGE.md", "docs/PRIVACY.md", "docs/BUILD.md", "LICENSE")
APP = "usr/share/system-device-monitor"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script("build_deb")


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


class StagePackage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/"project"
        self.stage = Path(temp.name)/"stage"
        self.stage.mkdir()
        files = ("app.py", "lib/mod.py", "helper/tool", "helper/rule.rules",
                 "packaging/postinst", *DOCS)
        for name in files:
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic " + name + "\n")
        self.manifest = {
            "format": 2,
            "runtime": ["app.py", "lib/mod.py"],
            "system": [
                {"source": "helper/tool", "target": "/usr/libexec/system-device-monitor/tool",
                 "mode": "0755"},
                {"source": "helper/rule.rules",
                 "target": "/usr/share/polkit-1/actions/tw.local.example.policy"},
            ],
            "maintainer": {"postinst": "packaging/postinst"},
            "source": sorted(files),
        }

    def stage_with(self, manifest):
        build.stage_package(manifest, self.stage, root=self.root, version="9.8.7", epoch=1234)

    def rejected(self, manifest):
        with self.assertRaises(ValueError):
            self.stage_with(manifest)

    def test_modes_follow_manifest(self):
        self.stage_with(self.manifest)
        expected = {
            APP + "/app.py": 0o644, APP + "/lib/mod.py": 0o644,
            "usr/libexec/system-device-monitor/tool": 0o755,
            "usr/share/polkit-1/actions/tw.local.example.policy": 0o644,
            "DEBIAN/postinst": 0o755, "DEBIAN/control": 0o644,
            "usr/bin/system-device-monitor": 0o755,
            "usr/share/applications/system-device-monitor.desktop": 0o644,
            "usr/share/doc/system-device-monitor/copyright": 0o644,
            "usr/share/doc/system-device-monitor/docs/USAGE.md": 0o644,
        }
        for name, wanted in expected.items():
            with self.subTest(name=name):
                self.assertEqual(oct(mode(self.stage/name)), oct(wanted))
        self.assertEqual((self.stage/"DEBIAN/postinst").read_text(), "synthetic packaging/postinst\n")

    def test_directories_and_mtimes_are_normalized(self):
        self.stage_with(self.manifest)
        for path in [self.stage, *self.stage.rglob("*")]:
            with self.subTest(path=str(path.relative_to(self.stage))):
                self.assertEqual(int(path.lstat().st_mtime), 1234)
                if path.is_dir():
                    self.assertEqual(oct(mode(path)), oct(0o755))
                else:
                    self.assertFalse(path.is_symlink())

    def test_system_target_outside_allowlist_is_rejected(self):
        for target in ("/etc/sudoers.d/x", "/usr/bin/sudo", "/etc/ld.so.preload",
                       "/usr/libexec/other/x", "/usr/libexec/system-device-monitor",
                       "/usr/libexec/system-device-monitor-evil/x", "/etc/polkit-1/rules.d/x",
                       "/opt/tool", "/var/lib/tool", "usr/libexec/system-device-monitor/x",
                       "/usr/libexec/system-device-monitor/../other/x",
                       "/usr/libexec/system-device-monitor/./x",
                       "/usr/libexec/system-device-monitor/x/", "", None,
                       "/usr/share/applications/system-device-monitor.desktop"):
            with self.subTest(target=target):
                manifest = copy.deepcopy(self.manifest)
                manifest["system"][0]["target"] = target
                self.rejected(manifest)

    def test_allowlisted_system_targets_are_accepted(self):
        for prefix in ("/usr/libexec/system-device-monitor", "/usr/share/system-device-monitor",
                       "/usr/share/polkit-1/actions", "/usr/lib/systemd/system",
                       "/usr/share/applications", "/usr/share/icons/hicolor"):
            with self.subTest(prefix=prefix):
                self.assertEqual(str(build.system_target(prefix + "/sub/file")),
                                 prefix.lstrip("/") + "/sub/file")
        manifest = copy.deepcopy(self.manifest)
        manifest["system"][1]["target"] = "/usr/share/icons/hicolor/scalable/apps/example.svg"
        self.stage_with(manifest)
        self.assertEqual(mode(self.stage/"usr/share/icons/hicolor/scalable/apps/example.svg"), 0o644)

    def test_unsafe_system_modes_are_rejected(self):
        for value in ("4755", "0777", "0666", "755x", "0200", 493):
            with self.subTest(mode=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["system"][0]["mode"] = value
                self.rejected(manifest)

    def test_symlink_sources_are_rejected(self):
        os.symlink(self.root/"app.py", self.root/"link.py")
        os.symlink(self.root/"lib", self.root/"linked")
        for section, value in (("runtime", ["link.py"]), ("runtime", ["linked/mod.py"]),
                               ("system", [{"source": "link.py",
                                            "target": "/usr/libexec/system-device-monitor/x"}]),
                               ("maintainer", {"postinst": "link.py"})):
            with self.subTest(section=section, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest[section] = value
                manifest["source"] = sorted({*manifest["source"], "link.py", "linked/mod.py"})
                self.rejected(manifest)

    def test_paths_escaping_the_project_are_rejected(self):
        outside = self.root.parent/"outside.py"
        outside.write_text("synthetic\n")
        for name in ("../outside.py", str(outside), "./app.py", "lib//mod.py", "missing.py"):
            with self.subTest(name=name):
                manifest = copy.deepcopy(self.manifest)
                manifest["runtime"] = [name]
                manifest["source"].append(name)
                self.rejected(manifest)

    def test_maintainer_scripts_are_restricted(self):
        for value in ({"config": "packaging/postinst"}, {"postinst; rm": "packaging/postinst"},
                      ["packaging/postinst"]):
            with self.subTest(value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["maintainer"] = value
                self.rejected(manifest)

    def test_packaged_files_must_be_public_sources(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["source"].remove("helper/tool")
        self.rejected(manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["source"].remove("packaging/postinst")
        self.rejected(manifest)

    def test_unknown_manifest_format_is_rejected(self):
        for value in (None, 1, "2"):
            with self.subTest(format=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["format"] = value
                self.rejected(manifest)

    def test_duplicate_targets_are_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["system"][1]["target"] = manifest["system"][0]["target"]
        self.rejected(manifest)

    def test_optional_sections_may_be_empty(self):
        manifest = dict(self.manifest, system=[], maintainer={})
        self.stage_with(manifest)
        self.assertEqual(sorted(p.name for p in (self.stage/"DEBIAN").iterdir()), ["control"])


class TextEncoding(unittest.TestCase):
    def test_text_file_calls_name_utf8(self):
        tree = ast.parse((ROOT/"scripts/build_deb.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr in ("read_text", "write_text")]
        self.assertTrue(calls)
        for call in calls:
            with self.subTest(line=call.lineno):
                encodings = [k.value for k in call.keywords if k.arg == "encoding"]
                self.assertEqual([getattr(value, "value", None) for value in encodings], ["utf-8"])


class ControlText(unittest.TestCase):
    def test_depends_adds_libnotify_and_keeps_the_rest(self):
        text = build.control_text("1.2.3")
        depends = next(line for line in text.splitlines() if line.startswith("Depends: "))
        self.assertIn("gir1.2-notify-0.7", depends)
        self.assertEqual(
            depends,
            "Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, "
            "gir1.2-gudev-1.0, gir1.2-ayatanaappindicator3-0.1, gir1.2-notify-0.7, "
            "librsvg2-common, util-linux")
        self.assertIn("Package: system-device-monitor\nVersion: 1.2.3\n", text)
        self.assertTrue(text.endswith(" GPU, network and currently connected peripherals. No telemetry.\n"))


if __name__ == "__main__":
    unittest.main()
