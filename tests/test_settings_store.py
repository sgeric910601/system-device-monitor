"""設定視窗的唯一寫入點：驗證 → 更新 → 通知 → 延遲 500 毫秒寫檔。

所有檔案都在暫存目錄；計時器是假的，由測試決定何時觸發。
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm.constants import Storage
from sdm.core.settings import LEGACY_DEFAULTS, Preferences, PreferencesReadOnlyError
from sdm.services import settings_store
from sdm.services.settings_store import READ_ONLY, SAVE_FAILED, STATUS, SettingsStore


class FakeTimer:
    def __init__(self):
        self.pending, self.next_id, self.cancelled = {}, 1, []

    def schedule(self, milliseconds, callback):
        source = self.next_id
        self.next_id += 1
        self.pending[source] = (milliseconds, callback)
        return source

    def cancel(self, source):
        self.cancelled.append(source)
        self.pending.pop(source, None)

    def fire(self):
        for source, (_, callback) in list(self.pending.items()):
            del self.pending[source]
            callback()


class Store(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.legacy_path, self.prefs_path = self.dir/"settings.json", self.dir/"preferences.json"
        self.timer = FakeTimer()
        self.autostart = []
        self.events = []

    def store(self, preferences=None, persist=True, autostart=False):
        store = SettingsStore(dict(LEGACY_DEFAULTS), preferences or Preferences.defaults(),
                              self.legacy_path, self.prefs_path, self.timer.schedule,
                              self.timer.cancel, persist=persist, autostart=autostart,
                              write_autostart=self.autostart.append)
        store.subscribe(lambda changed, current: self.events.append(set(changed)))
        return store

    def test_preference_is_validated_applied_and_saved_after_the_delay(self):
        store = self.store()
        self.assertEqual(store.update(refresh_interval=5), frozenset({"refresh_interval"}))
        self.assertEqual(store.preferences.refresh_interval, 5)
        self.assertEqual(self.events, [{"refresh_interval"}])
        self.assertFalse(self.prefs_path.exists())
        self.assertEqual([delay for delay, _ in self.timer.pending.values()],
                         [Storage.SAVE_DELAY_MS])
        self.timer.fire()
        self.assertEqual(json.loads(self.prefs_path.read_text())["refresh_interval"], 5)
        self.assertFalse(self.legacy_path.exists())

    def test_quick_changes_are_saved_once(self):
        store = self.store()
        with patch.object(settings_store, "save_preferences") as save:
            store.update(refresh_interval=5)
            store.update(refresh_interval=10)
            self.assertEqual(len(self.timer.pending), 1)
            self.timer.fire()
        save.assert_called_once()
        self.assertEqual(save.call_args[0][1].refresh_interval, 10)

    def test_legacy_items_go_to_the_old_file_in_its_shape(self):
        store = self.store()
        store.update(cpu=False)
        self.timer.fire()
        self.assertEqual(json.loads(self.legacy_path.read_text()),
                         {"cpu": False, "temperature": True, "ram": True, "gpu": True,
                          "network": True})
        self.assertFalse(self.prefs_path.exists())
        self.assertFalse(store.legacy["cpu"])

    def test_invalid_values_are_rejected_without_changes(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.update(refresh_interval=3)
        with self.assertRaises(ValueError):
            store.update(cpu="yes")
        with self.assertRaises(KeyError):
            store.update(unknown=True)
        self.assertEqual(self.events, [])
        self.assertEqual(self.timer.pending, {})

    def test_same_value_changes_nothing(self):
        store = self.store()
        self.assertEqual(store.update(refresh_interval=2, cpu=True), frozenset())
        self.assertEqual((self.events, self.timer.pending), ([], {}))

    def test_read_only_preferences_refuse_changes_but_legacy_still_works(self):
        store = self.store(Preferences.defaults(read_only=True))
        with self.assertRaises(PreferencesReadOnlyError):
            store.update(refresh_interval=5)
        self.assertEqual(store.preferences.refresh_interval, 2)
        self.assertTrue(store.read_only)
        store.update(ram=False)
        self.timer.fire()
        self.assertFalse(json.loads(self.legacy_path.read_text())["ram"])
        self.assertEqual(store.problem, READ_ONLY)

    def test_write_failure_keeps_the_value_and_reports_it(self):
        store = self.store()
        store.update(power_saving=True)
        with patch.object(settings_store, "save_preferences", side_effect=OSError("full")):
            with self.assertLogs("sdm.services.settings_store", "WARNING"):
                self.timer.fire()
        self.assertTrue(store.preferences.power_saving)
        self.assertEqual(store.problem, SAVE_FAILED)
        self.assertEqual(self.events[-1], {STATUS})

    def test_read_only_detected_while_saving_is_reported(self):
        store = self.store()
        store.update(power_saving=True)
        with patch.object(settings_store, "save_preferences",
                          side_effect=PreferencesReadOnlyError("newer")):
            self.timer.fire()
        self.assertEqual(store.problem, READ_ONLY)
        self.assertTrue(store.read_only)

    def test_preview_never_writes(self):
        store = self.store(persist=False)
        store.update(refresh_interval=10, cpu=False, autostart=True)
        self.assertEqual(self.timer.pending, {})
        store.flush()
        self.assertEqual(list(self.dir.iterdir()), [])
        self.assertEqual(self.autostart, [])
        self.assertTrue(store.autostart)

    def test_autostart_goes_through_the_same_path(self):
        store = self.store()
        store.update(autostart=True)
        self.assertEqual(self.autostart, [])
        self.timer.fire()
        self.assertEqual(self.autostart, [True])
        with self.assertRaises(ValueError):
            store.update(autostart=1)

    def test_flush_writes_pending_changes_at_once(self):
        store = self.store()
        store.update(tray_mode="merged")
        store.flush()
        self.assertEqual(self.timer.pending, {})
        self.assertEqual(json.loads(self.prefs_path.read_text())["tray_mode"], "merged")
        store.flush()       # 沒有待寫入的變更時不做任何事


if __name__ == "__main__":
    unittest.main()
