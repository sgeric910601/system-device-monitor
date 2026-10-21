"""設定相容性測試：舊版 settings.json 格式不變，新設定存在 preferences.json。

所有檔案都在暫存目錄；sdm.paths 也替換成暫存位置，避免誤碰真實 XDG 目錄。
"""
import dataclasses
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm import paths
from sdm.core.settings import (LEGACY_DEFAULTS, LEGACY_KEYS, PREFERENCE_SPECS, Preferences,
                               load_legacy, load_preferences, load_settings, save_legacy,
                               save_preferences)

LOGGER = "sdm.core.settings"
PERIPHERAL = "0123456789abcdef"


def legacy_text(values):
    return json.dumps(values, ensure_ascii=False, indent=2) + "\n"


class TempConfig(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)/"config/system-device-monitor"
        self.dir.mkdir(parents=True)
        self.legacy = self.dir/"settings.json"
        self.prefs = self.dir/"preferences.json"
        self.autostart = Path(temp.name)/"config/autostart/system-device-monitor.desktop"
        guard = patch.multiple(paths, CONFIG=self.legacy, PREFERENCES=self.prefs,
                               AUTOSTART=self.autostart)
        guard.start()
        self.addCleanup(guard.stop)

    def write_prefs(self, data):
        self.prefs.write_text(json.dumps(data))

    def load_with_warning(self, data, key):
        self.write_prefs(data)
        with self.assertLogs(LOGGER, "WARNING") as logs:
            loaded = load_preferences(self.prefs)
        self.assertTrue(any(key in line for line in logs.output), logs.output)
        return loaded


class LegacySettings(TempConfig):
    def test_false_value_is_kept_with_all_five_keys(self):
        self.legacy.write_text(legacy_text({"cpu": False, "ram": True}))
        loaded = load_legacy(self.legacy)
        self.assertEqual(tuple(loaded), LEGACY_KEYS)
        self.assertFalse(loaded["cpu"])
        self.assertTrue(all(loaded[key] for key in LEGACY_KEYS if key != "cpu"))

    def test_written_back_in_legacy_shape_only(self):
        self.legacy.write_text(json.dumps({"cpu": False, "gpu": "x", "future": 1}))
        save_legacy(self.legacy, load_legacy(self.legacy))
        expected = dict(cpu=False, temperature=True, ram=True, gpu=True, network=True)
        self.assertEqual(self.legacy.read_text(), legacy_text(expected))

    def test_wrong_typed_values_are_ignored(self):
        self.legacy.write_text(json.dumps({"cpu": "no", "ram": 0, "gpu": None}))
        self.assertEqual(dict(load_legacy(self.legacy)), dict(LEGACY_DEFAULTS))

    def test_missing_or_corrupt_file_gives_defaults(self):
        self.assertEqual(dict(load_legacy(self.legacy)), dict(LEGACY_DEFAULTS))
        self.legacy.write_text("{not json")
        self.assertEqual(dict(load_legacy(self.legacy)), dict(LEGACY_DEFAULTS))

    def test_save_rejects_non_boolean_values(self):
        with self.assertRaises(ValueError):
            save_legacy(self.legacy, {**LEGACY_DEFAULTS, "cpu": 1})
        self.assertFalse(self.legacy.exists())

    def test_defaults_are_immutable(self):
        with self.assertRaises(TypeError):
            LEGACY_DEFAULTS["cpu"] = False


class PreferenceDefaults(TempConfig):
    def test_missing_file_gives_defaults_without_writing(self):
        with self.assertNoLogs(LOGGER, "WARNING"):
            loaded = load_preferences(self.prefs)
        self.assertEqual(loaded, Preferences.defaults())
        self.assertFalse(self.prefs.exists())

    def test_documented_defaults(self):
        prefs = Preferences.defaults()
        self.assertEqual(len(PREFERENCE_SPECS), 35)
        expected = dict(tray_mode="split", tray_display="icon_percent", refresh_interval=2,
                        tray_power=True,
                        power_saving=False, system_monitors=True, low_threshold=20,
                        full_threshold=100, ups_low_threshold=50, app_icon_color="mint",
                        companion_appearance="cat", desktop_pet_scale=1.0,
                        charging_target_percent=0, charging_hold_percent=80,
                        charging_sleep_strategy="readiness_first", charge_limit_persist=False)
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(prefs[key], value)
                self.assertIs(type(prefs[key]), type(value))
        self.assertEqual(prefs.hidden_flow_items, ())
        self.assertEqual(dict(prefs.peripheral_thresholds), {})

    def test_corrupt_file_gives_defaults_and_warns(self):
        self.prefs.write_text("[1, 2")
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertEqual(load_preferences(self.prefs), Preferences.defaults())
        self.prefs.write_text("[1, 2]")
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertEqual(load_preferences(self.prefs), Preferences.defaults())

    def test_defaults_round_trip(self):
        save_preferences(self.prefs, Preferences.defaults())
        data = json.loads(self.prefs.read_text())
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["hidden_flow_items"], [])
        self.assertEqual(data["peripheral_thresholds"], {})
        self.assertEqual(load_preferences(self.prefs), Preferences.defaults())


class PreferenceValidation(TempConfig):
    def test_invalid_value_falls_back_and_logs_key_only(self):
        self.write_prefs({"schema": 2, "tray_mode": "sideways-secret", "low_threshold": 30})
        with self.assertLogs(LOGGER, "WARNING") as logs:
            loaded = load_preferences(self.prefs)
        self.assertEqual(loaded.tray_mode, "split")
        self.assertEqual(loaded.low_threshold, 30)
        text = "\n".join(logs.output)
        self.assertIn("tray_mode", text)
        self.assertNotIn("sideways-secret", text)

    def test_boolean_given_as_integer_is_rejected(self):
        self.assertFalse(self.load_with_warning({"power_saving": 1}, "power_saving").power_saving)
        self.assertEqual(self.load_with_warning({"refresh_interval": True},
                                                "refresh_interval").refresh_interval, 2)
        with self.assertRaises(ValueError):
            Preferences.defaults().with_changes(power_saving=1)

    def test_out_of_range_thresholds_are_rejected(self):
        cases = [("low_threshold", 55, 20), ("low_threshold", 0, 20), ("low_threshold", 22, 20),
                 ("full_threshold", 75, 100), ("full_threshold", 95.0, 100),
                 ("ups_low_threshold", 95, 50), ("charging_target_percent", 30, 0),
                 ("charging_hold_percent", 101, 80), ("desktop_pet_scale", 3, 1.0),
                 ("desktop_pet_scale", float("nan"), 1.0)]
        for key, raw, default in cases:
            with self.subTest(key=key, raw=raw):
                self.assertEqual(self.load_with_warning({key: raw}, key)[key], default)

    def test_in_range_values_are_accepted(self):
        prefs = Preferences.defaults().with_changes(
            low_threshold=5, full_threshold=80, ups_low_threshold=13,
            charging_target_percent=0, charging_hold_percent=50, desktop_pet_scale=2)
        self.assertEqual(prefs.charging_target_percent, 0)
        self.assertEqual(prefs.with_changes(charging_target_percent=65).charging_target_percent, 65)
        self.assertEqual(prefs.desktop_pet_scale, 2.0)
        self.assertIs(type(prefs.desktop_pet_scale), float)

    def test_tray_power_must_be_a_boolean(self):
        for raw in (1, "yes", None, [True]):
            with self.subTest(raw=raw):
                self.assertIs(self.load_with_warning({"tray_power": raw}, "tray_power").tray_power,
                              True)
                with self.assertRaises(ValueError):
                    Preferences.defaults().with_changes(tray_power=raw)
        self.assertIs(Preferences.defaults().with_changes(tray_power=False).tray_power, False)

    def test_tray_power_is_not_a_legacy_setting(self):
        self.assertNotIn("tray_power", LEGACY_KEYS)
        prefs = Preferences.defaults().with_changes(tray_power=False)
        save_preferences(self.prefs, prefs)
        self.assertIs(json.loads(self.prefs.read_text())["tray_power"], False)
        self.assertFalse(self.legacy.exists())

    def test_unknown_enum_values_are_rejected(self):
        cases = [("app_icon_color", "pink", "mint"), ("companion_appearance", "dog", "cat"),
                 ("refresh_interval", 3, 2), ("tray_display", "icon", "icon_percent"),
                 ("charging_sleep_strategy", "", "readiness_first"), ("tray_mode", None, "split")]
        for key, raw, default in cases:
            with self.subTest(key=key):
                self.assertEqual(self.load_with_warning({key: raw}, key)[key], default)
                with self.assertRaises(ValueError):
                    Preferences.defaults().with_changes(**{key: raw})

    def test_peripheral_threshold_invalid_entries_are_dropped(self):
        raw = {PERIPHERAL: 20, "XYZ": 10, PERIPHERAL.upper(): 10,
               "fedcba9876543210": 25, "aaaaaaaaaaaaaaaa": True, "bbbbbbbbbbbbbbbb": 0}
        self.write_prefs({"peripheral_thresholds": raw})
        with self.assertLogs(LOGGER, "WARNING") as logs:
            loaded = load_preferences(self.prefs)
        self.assertEqual(dict(loaded.peripheral_thresholds),
                         {PERIPHERAL: 20, "bbbbbbbbbbbbbbbb": 0})
        self.assertNotIn("fedcba9876543210", "\n".join(logs.output))
        self.assertEqual(self.load_with_warning({"peripheral_thresholds": [1]},
                                                "peripheral_thresholds").peripheral_thresholds, {})
        with self.assertRaises(ValueError):
            Preferences.defaults().with_changes(peripheral_thresholds={"XYZ": 10})

    def test_name_lists_are_deduplicated_and_bounded(self):
        loaded = self.load_with_warning(
            {"hidden_flow_items": ["gpu", "gpu", "Bad", "x" * 33, 5, "ups_load"]},
            "hidden_flow_items")
        self.assertEqual(loaded.hidden_flow_items, ("gpu", "ups_load"))
        too_many = ["a" * (i % 30 + 1) + "_" * (i // 30) for i in range(65)]
        self.assertEqual(self.load_with_warning({"hidden_detail_fields": too_many},
                                                "hidden_detail_fields").hidden_detail_fields, ())
        prefs = Preferences.defaults().with_changes(hidden_detail_fields=["cycle", "cycle"])
        self.assertEqual(prefs.hidden_detail_fields, ("cycle",))
        with self.assertRaises(ValueError):
            Preferences.defaults().with_changes(hidden_detail_fields=["Cycle"])


class PreferenceValue(TempConfig):
    def test_with_changes_returns_validated_copy(self):
        original = Preferences.defaults()
        changed = original.with_changes(tray_mode="merged", low_threshold=30)
        self.assertEqual((changed.tray_mode, changed.low_threshold), ("merged", 30))
        self.assertEqual((original.tray_mode, original.low_threshold), ("split", 20))
        with self.assertRaises(KeyError):
            original.with_changes(not_a_setting=True)

    def test_value_is_immutable(self):
        prefs = Preferences.defaults().with_changes(peripheral_thresholds={PERIPHERAL: 10})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            prefs.tray_mode = "merged"
        with self.assertRaises(TypeError):
            prefs.values["tray_mode"] = "merged"
        with self.assertRaises(TypeError):
            prefs.peripheral_thresholds[PERIPHERAL] = 50
        with self.assertRaises(AttributeError):
            prefs.hidden_flow_items.append("gpu")

    def test_unknown_keys_survive_a_save(self):
        self.write_prefs({"schema": 2, "future_key": {"nested": [1, "a"]}, "low_threshold": 30})
        loaded = load_preferences(self.prefs)
        save_preferences(self.prefs, loaded.with_changes(low_threshold=25))
        data = json.loads(self.prefs.read_text())
        self.assertEqual(data["future_key"], {"nested": [1, "a"]})
        self.assertEqual(data["low_threshold"], 25)
        self.assertEqual(data["schema"], 2)

    def test_invalid_schema_number_is_replaced(self):
        for raw in (1, "2", True, None):
            with self.subTest(raw=raw):
                self.assertEqual(self.load_with_warning({"schema": raw}, "schema").schema, 2)

    def test_wrong_container_types_fall_back(self):
        self.assertEqual(self.load_with_warning({"hidden_flow_items": "gpu"},
                                                "hidden_flow_items").hidden_flow_items, ())
        many = {f"{i:016x}": 10 for i in range(65)}
        self.assertEqual(dict(self.load_with_warning({"peripheral_thresholds": many},
                                                     "peripheral_thresholds").peripheral_thresholds), {})

    def test_unknown_attribute_and_incomplete_values_are_rejected(self):
        prefs = Preferences.defaults()
        with self.assertRaises(AttributeError):
            prefs.not_a_setting
        with self.assertRaises(AttributeError):
            prefs._private
        with self.assertRaises(ValueError):
            Preferences({"tray_mode": "split"})


class FirstRun(TempConfig):
    def test_first_run_only_when_legacy_file_is_absent(self):
        self.write_prefs({"schema": 2})
        self.assertTrue(load_settings(self.legacy, self.prefs).first_run)
        self.prefs.unlink()
        self.legacy.write_text(legacy_text(dict(LEGACY_DEFAULTS)))
        loaded = load_settings(self.legacy, self.prefs)
        self.assertFalse(loaded.first_run)
        self.assertEqual(loaded.preferences, Preferences.defaults())
        self.assertEqual(dict(loaded.legacy), dict(LEGACY_DEFAULTS))

    def test_loading_creates_no_files(self):
        load_settings(self.legacy, self.prefs)
        self.assertEqual(list(self.dir.iterdir()), [])


class AtomicSave(TempConfig):
    def snapshot(self, path):
        return path.read_bytes(), path.stat().st_mtime_ns

    def test_save_leaves_no_temporary_file(self):
        save_preferences(self.prefs, Preferences.defaults())
        save_legacy(self.legacy, LEGACY_DEFAULTS)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["preferences.json", "settings.json"])

    def test_failed_write_keeps_original_files(self):
        self.write_prefs({"schema": 2, "low_threshold": 30})
        self.legacy.write_text(legacy_text(dict(LEGACY_DEFAULTS)))
        before = (self.prefs.read_bytes(), self.legacy.read_bytes())
        with patch("sdm.core.jsonfile.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_preferences(self.prefs, Preferences.defaults())
            with self.assertRaises(OSError):
                save_legacy(self.legacy, {**LEGACY_DEFAULTS, "cpu": False})
        self.assertEqual((self.prefs.read_bytes(), self.legacy.read_bytes()), before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["preferences.json", "settings.json"])

    def test_each_save_touches_only_its_own_file(self):
        self.legacy.write_text(legacy_text(dict(LEGACY_DEFAULTS)))
        self.write_prefs({"schema": 2})
        os.utime(self.legacy, ns=(1, 1))
        os.utime(self.prefs, ns=(1, 1))
        legacy_before = self.snapshot(self.legacy)
        save_preferences(self.prefs, Preferences.defaults().with_changes(low_threshold=10))
        self.assertEqual(self.snapshot(self.legacy), legacy_before)
        prefs_before = self.snapshot(self.prefs)
        save_legacy(self.legacy, {**LEGACY_DEFAULTS, "gpu": False})
        self.assertEqual(self.snapshot(self.prefs), prefs_before)

    def test_autostart_file_untouched_by_load_and_save(self):
        self.autostart.parent.mkdir(parents=True)
        self.autostart.write_text("[Desktop Entry]\n")
        os.utime(self.autostart, ns=(1, 1))
        before = self.snapshot(self.autostart)
        loaded = load_settings(self.legacy, self.prefs)
        save_legacy(self.legacy, loaded.legacy)
        save_preferences(self.prefs, loaded.preferences)
        self.assertEqual(self.snapshot(self.autostart), before)
        self.assertEqual(list(self.autostart.parent.iterdir()), [self.autostart])


if __name__ == "__main__":
    unittest.main()
