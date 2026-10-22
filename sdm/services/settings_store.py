"""設定的唯一寫入點：驗證 → 更新記憶體 → 通知 → 延遲 500 毫秒寫檔。

- 舊版五個頂端列項目（cpu、temperature、ram、gpu、network）寫回 settings.json，維持舊格式；
  其他設定寫入 preferences.json；登入自動啟動寫 autostart 桌面檔。
- 每次變更都立即套用並通知訂閱者（不需重新啟動）；連續調整在最後一次之後 500 毫秒才寫一次檔。
- preferences.json 唯讀（較新版本建立或無法安全備份）時拒絕變更新設定，舊版項目仍可變更。
- 寫檔失敗時保留這次執行的選擇，problem 說明原因；示範模式（persist=False）完全不寫檔。
計時器由外部提供：schedule(毫秒, 回呼) 回傳來源代號、cancel(代號) 取消。
"""
import logging
from types import MappingProxyType

from sdm.constants import Storage
from sdm.core.settings import (LEGACY_KEYS, PreferencesReadOnlyError, save_legacy,
                               save_preferences)

LOG = logging.getLogger(__name__)
AUTOSTART = "autostart"
STATUS = "_status"          # 通知訂閱者：寫檔狀態改變（problem）
READ_ONLY = "設定檔目前無法寫入（可能由較新版本建立），這些項目暫時無法變更"
SAVE_FAILED = "無法儲存設定：這次執行期間仍會套用，重新啟動後恢復原本的選擇"


def checked_bool(key, value):
    if type(value) is not bool:
        raise ValueError("Setting must be a boolean: " + key)
    return value


class SettingsStore:
    def __init__(self, legacy, preferences, legacy_path, preferences_path, schedule, cancel,
                 persist=True, autostart=False, write_autostart=lambda enabled: None):
        self.legacy = MappingProxyType(dict(legacy))
        self.preferences = preferences
        self.autostart = autostart
        self.legacy_path, self.preferences_path = legacy_path, preferences_path
        self.schedule, self.cancel = schedule, cancel
        self.persist, self.write_autostart = persist, write_autostart
        self.problem = READ_ONLY if preferences.read_only else None
        self.dirty, self.timer, self.listeners = frozenset(), None, []

    @property
    def read_only(self):
        return self.preferences.read_only

    def subscribe(self, listener):
        """listener(changed, store)：changed 為改變的鍵（寫檔狀態改變時為 {STATUS}）。"""
        self.listeners.append(listener)

    def value(self, key):
        if key in LEGACY_KEYS:
            return self.legacy[key]
        if key == AUTOSTART:
            return self.autostart
        return self.preferences[key]

    def update(self, **changes):
        """驗證後套用；回傳實際改變的鍵。無效值拋出 ValueError／KeyError，唯讀拋出
        PreferencesReadOnlyError，兩者都不會改變任何設定。"""
        legacy = {key: checked_bool(key, value) for key, value in changes.items()
                  if key in LEGACY_KEYS}
        autostart = (checked_bool(AUTOSTART, changes[AUTOSTART])
                     if AUTOSTART in changes else self.autostart)
        others = {key: value for key, value in changes.items()
                  if key not in LEGACY_KEYS and key != AUTOSTART}
        preferences = self.preferences.with_changes(**others) if others else self.preferences
        changed = frozenset(key for key in changes if self.value(key) != (
            legacy.get(key) if key in LEGACY_KEYS else
            autostart if key == AUTOSTART else preferences[key]))
        if not changed:
            return changed
        if self.read_only and changed & set(others):
            raise PreferencesReadOnlyError("Preferences are read-only")
        self.legacy = MappingProxyType({**self.legacy, **legacy})
        self.preferences, self.autostart = preferences, autostart
        self.dirty = self.dirty | changed
        self.restart_timer()
        self.notify(changed)
        return changed

    def restart_timer(self):
        if not self.persist:
            self.dirty = frozenset()
            return
        if self.timer is not None:
            self.cancel(self.timer)
        self.timer = self.schedule(Storage.SAVE_DELAY_MS, self.timer_fired)

    def timer_fired(self):
        self.timer = None
        self.save()
        return False

    def flush(self):
        """結束前立即寫入還沒寫的變更。"""
        if self.timer is not None:
            self.cancel(self.timer)
            self.timer = None
        if self.dirty:
            self.save()

    def save(self):
        dirty, self.dirty = self.dirty, frozenset()
        problem = None
        try:
            if dirty & set(LEGACY_KEYS):
                save_legacy(self.legacy_path, self.legacy)
            if AUTOSTART in dirty:
                self.write_autostart(self.autostart)
            if dirty - set(LEGACY_KEYS) - {AUTOSTART}:
                save_preferences(self.preferences_path, self.preferences)
        except PreferencesReadOnlyError:
            self.preferences = type(self.preferences)(self.preferences.values,
                                                      self.preferences.extra,
                                                      self.preferences.schema, read_only=True)
            problem = READ_ONLY
        except OSError as error:
            LOG.warning("Cannot save settings: %s", type(error).__name__)
            problem = SAVE_FAILED
        problem = problem or (READ_ONLY if self.read_only else None)
        if problem != self.problem:
            self.problem = problem
            self.notify(frozenset({STATUS}))

    def notify(self, changed):
        for listener in tuple(self.listeners):
            listener(changed, self)
