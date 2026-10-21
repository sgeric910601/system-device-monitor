"""設定檔保護：損壞檔先備份、無法備份或較新版本時唯讀、殘留暫存檔清理、檔案描述子不外洩。

所有檔案都在暫存目錄；sdm.paths 也替換成暫存位置，避免誤碰真實 XDG 目錄。
"""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from sdm import paths
from sdm.core.settings import (LEGACY_DEFAULTS, Preferences, PreferencesReadOnlyError,
                               load_preferences, load_settings, save_legacy, save_preferences)

LOGGER = "sdm.core.settings"
EPOCH = 1700000000
HOUR = 3600


def fixed_clock():
    return EPOCH + 0.75


class TempConfig(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)/"config/system-device-monitor"
        self.dir.mkdir(parents=True)
        self.legacy = self.dir/"settings.json"
        self.prefs = self.dir/"preferences.json"
        guard = patch.multiple(paths, CONFIG=self.legacy, PREFERENCES=self.prefs,
                               AUTOSTART=Path(temp.name)/"config/autostart/x.desktop")
        guard.start()
        self.addCleanup(guard.stop)

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def load_corrupt(self, data):
        self.prefs.write_bytes(data)
        with self.assertLogs(LOGGER, "WARNING"):
            return load_preferences(self.prefs, clock=fixed_clock)


class CorruptBackup(TempConfig):
    def test_corrupt_file_is_backed_up_before_any_save(self):
        data = b'{"low_threshold": 30,\xff broken'
        loaded = self.load_corrupt(data)
        backup = self.dir/f"preferences-{EPOCH}.json.bak"
        self.assertEqual(backup.read_bytes(), data)
        self.assertEqual(self.prefs.read_bytes(), data)
        self.assertEqual(loaded, Preferences.defaults())
        self.assertFalse(loaded.read_only)
        save_preferences(self.prefs, loaded)
        self.assertEqual(json.loads(self.prefs.read_text())["schema"], 2)
        self.assertEqual(backup.read_bytes(), data)

    def test_non_object_json_is_backed_up(self):
        self.load_corrupt(b"[1, 2]")
        self.assertEqual((self.dir/f"preferences-{EPOCH}.json.bak").read_bytes(), b"[1, 2]")

    def test_second_corruption_gets_a_distinct_backup_name(self):
        self.load_corrupt(b"{first")
        self.load_corrupt(b"{second")
        self.assertEqual(self.names(), sorted(["preferences.json", f"preferences-{EPOCH}.json.bak",
                                               f"preferences-{EPOCH}-1.json.bak"]))
        self.assertEqual((self.dir/f"preferences-{EPOCH}.json.bak").read_bytes(), b"{first")
        self.assertEqual((self.dir/f"preferences-{EPOCH}-1.json.bak").read_bytes(), b"{second")

    def test_failed_backup_makes_preferences_read_only(self):
        self.prefs.write_bytes(b"{broken")
        with patch("sdm.core.settings.create_backup", side_effect=OSError("read-only fs")):
            with self.assertLogs(LOGGER, "WARNING"):
                loaded = load_preferences(self.prefs, clock=fixed_clock)
        self.assertTrue(loaded.read_only)
        with self.assertRaises(PreferencesReadOnlyError):
            save_preferences(self.prefs, loaded.with_changes(low_threshold=10))
        self.assertEqual(self.prefs.read_bytes(), b"{broken")
        self.assertEqual(self.names(), ["preferences.json"])

    def test_all_backup_names_taken_makes_preferences_read_only(self):
        for name in (f"preferences-{EPOCH}.json.bak", f"preferences-{EPOCH}-1.json.bak"):
            (self.dir/name).write_bytes(b"older backup")
        self.prefs.write_bytes(b"{broken")
        with patch("sdm.core.jsonfile.Storage.BACKUP_NAME_ATTEMPTS", 2):
            with self.assertLogs(LOGGER, "WARNING"):
                loaded = load_preferences(self.prefs, clock=fixed_clock)
        self.assertTrue(loaded.read_only)
        self.assertEqual((self.dir/f"preferences-{EPOCH}.json.bak").read_bytes(), b"older backup")
        self.assertEqual(self.prefs.read_bytes(), b"{broken")

    def test_unreadable_file_is_read_only(self):
        self.prefs.write_text(json.dumps({"schema": 2}))
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            with self.assertLogs(LOGGER, "WARNING"):
                loaded = load_preferences(self.prefs)
        self.assertTrue(loaded.read_only)
        with self.assertRaises(PreferencesReadOnlyError):
            save_preferences(self.prefs, loaded)

    def test_missing_file_is_writable_and_not_backed_up(self):
        loaded = load_preferences(self.prefs, clock=fixed_clock)
        self.assertFalse(loaded.read_only)
        self.assertEqual(self.names(), [])


class NewerSchema(TempConfig):
    def test_newer_schema_loads_known_values_but_refuses_to_save(self):
        data = json.dumps({"schema": 3, "low_threshold": 30, "future": {"x": 1}}).encode()
        self.prefs.write_bytes(data)
        with self.assertLogs(LOGGER, "WARNING"):
            loaded = load_preferences(self.prefs)
        self.assertTrue(loaded.read_only)
        self.assertEqual(loaded.low_threshold, 30)
        changed = loaded.with_changes(low_threshold=40)
        self.assertTrue(changed.read_only)
        with self.assertRaises(PreferencesReadOnlyError):
            save_preferences(self.prefs, changed)
        self.assertEqual(self.prefs.read_bytes(), data)
        self.assertEqual(self.names(), ["preferences.json"])


class StaleTemporaries(TempConfig):
    def make(self, name, age):
        path = self.dir/name
        path.write_text("partial")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_only_our_old_temporaries_are_removed(self):
        self.make(".settings.json.0123456789abcdef.tmp", 2*HOUR)
        self.make(".preferences.json.fedcba9876543210.tmp", 2*HOUR)
        kept = [self.make(".preferences.json.aaaaaaaaaaaaaaaa.tmp", 60),
                self.make(".preferences.json.short.tmp", 2*HOUR),
                self.make(".preferences.json.0123456789ABCDEF.tmp", 2*HOUR),
                self.make(".other.json.0123456789abcdef.tmp", 2*HOUR),
                self.make("settings.tmp", 2*HOUR),
                self.make("notes.tmp", 2*HOUR)]
        outside = Path(self.dir.parent)/"target.txt"
        outside.write_text("keep")
        link = self.dir/".settings.json.1111111111111111.tmp"
        link.symlink_to(outside)
        os.utime(link, (time.time() - 2*HOUR,)*2, follow_symlinks=False)
        folder = self.dir/".settings.json.2222222222222222.tmp"
        folder.mkdir()
        os.utime(folder, (time.time() - 2*HOUR,)*2)
        load_settings(self.legacy, self.prefs)
        expected = sorted([p.name for p in kept] + [link.name, folder.name])
        self.assertEqual(self.names(), expected)
        self.assertEqual(outside.read_text(), "keep")


    def test_missing_directory_and_failed_removal_are_tolerated(self):
        self.assertEqual(load_preferences(self.dir/"absent/preferences.json"),
                         Preferences.defaults())
        stale = self.make(".preferences.json.0123456789abcdef.tmp", 2*HOUR)
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            with self.assertLogs("sdm.core.jsonfile", "WARNING"):
                load_preferences(self.prefs)
        self.assertTrue(stale.exists())


class FileDescriptors(TempConfig):
    def open_descriptors(self):
        return len(os.listdir("/proc/self/fd"))

    def test_failed_fdopen_closes_descriptor_and_removes_temporary(self):
        save_legacy(self.legacy, LEGACY_DEFAULTS)
        before_bytes, before_fds = self.legacy.read_bytes(), self.open_descriptors()
        with patch("sdm.core.jsonfile.os.fdopen", side_effect=OSError("no memory")):
            with self.assertRaises(OSError):
                save_legacy(self.legacy, {**LEGACY_DEFAULTS, "cpu": False})
        self.assertEqual(self.open_descriptors(), before_fds)
        self.assertEqual(self.legacy.read_bytes(), before_bytes)
        self.assertEqual(self.names(), ["settings.json"])


    def test_failed_replace_keeps_original_and_removes_temporary(self):
        save_legacy(self.legacy, LEGACY_DEFAULTS)
        before = self.legacy.read_bytes()
        with patch("sdm.core.jsonfile.os.replace", side_effect=OSError("cross-device")):
            with self.assertRaises(OSError):
                save_legacy(self.legacy, {**LEGACY_DEFAULTS, "ram": False})
        self.assertEqual(self.legacy.read_bytes(), before)
        self.assertEqual(self.names(), ["settings.json"])


class ValueSemantics(unittest.TestCase):
    def test_equal_values_hash_equally(self):
        first = Preferences.defaults().with_changes(peripheral_thresholds={"0123456789abcdef": 10},
                                                    hidden_flow_items=["gpu"])
        second = Preferences.defaults().with_changes(hidden_flow_items=["gpu"],
                                                     peripheral_thresholds={"0123456789abcdef": 10})
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(len({first, second, Preferences.defaults()}), 2)
        self.assertNotEqual(first, Preferences.defaults())
        self.assertNotEqual(Preferences.defaults(), Preferences.defaults(read_only=True))
        self.assertNotEqual(Preferences.defaults(), "not preferences")


if __name__ == "__main__":
    unittest.main()
