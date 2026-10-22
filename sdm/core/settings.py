"""使用者設定：舊版 settings.json 與新版型別化 preferences.json。

相容規則：
- settings.json 維持舊格式，只存五個頂端列布林值；舊版與退回舊版都只讀寫它。
  檔案無法解析或不是物件時，第一次覆寫前先另存 settings-<epoch>.json.bak（絕不覆寫既有備份），
  備份失敗就不覆寫。
- 新設定存在同目錄的 preferences.json（{"schema": 2, ...}），舊版不會讀取或覆寫。
- 首次啟動只看 settings.json 是否存在；缺少 preferences.json 不算首次啟動。
- preferences.json 中不認得的鍵在儲存時原樣保留；無效值改用預設值並記錄鍵名。

保護規則：
- preferences.json 無法解析或不是物件時，先複製成新的
  preferences-<epoch>.json.bak（絕不覆寫既有備份）再使用預設值；
  無法讀取或無法備份時，載入結果標記為唯讀，儲存會被拒絕。
- 檔案的 schema 比目前支援的新時，仍讀取看得懂的值，但標記為唯讀，避免被舊版降級覆寫。
- 載入時清理本程式先前殘留、超過一小時的暫存檔。
"""
from dataclasses import dataclass, replace
import json
import logging
import time
from types import MappingProxyType
from typing import Mapping, NamedTuple

from sdm.core.jsonfile import (create_backup, load_json, remove_stale_temporaries,
                               write_json_atomic)
from sdm.core.preference_specs import PREFERENCE_SPECS, SCHEMA_VERSION

LOG = logging.getLogger(__name__)

LEGACY_KEYS = ("cpu", "temperature", "ram", "gpu", "network")
LEGACY_DEFAULTS = MappingProxyType({key: True for key in LEGACY_KEYS})
EMPTY = MappingProxyType({})


class PreferencesReadOnlyError(RuntimeError):
    """偏好設定為唯讀（來自較新版本，或原檔無法安全備份），拒絕覆寫。"""


def load_legacy(path):
    """讀取舊版五個布林值；缺少或型別不符的值沿用預設 True。"""
    remove_stale_temporaries(path)
    stored = load_json(path)
    return MappingProxyType({key: stored[key] if isinstance(stored.get(key), bool) else default
                             for key, default in LEGACY_DEFAULTS.items()})


def undecodable(path):
    """檔案存在但無法解析或不是 JSON 物件時回傳原始內容；不存在或讀不到時回傳 None。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if _decode_object(data) is None else None


def save_legacy(path, values, clock=time.time):
    """只寫回舊版格式的五個布林值，與舊版寫出的內容完全相同。

    原本的檔案無法解析時先另存備份；備份失敗會拋出 OSError，原檔保持不變。
    """
    data = {}
    for key in LEGACY_KEYS:
        if type(values.get(key)) is not bool:
            raise ValueError("Legacy setting must be a boolean: " + key)
        data[key] = values[key]
    damaged = undecodable(path)
    if damaged is not None:
        backup = create_backup(path, damaged, int(clock()))
        LOG.warning("Legacy settings file was invalid; copied to %s", backup.name)
    write_json_atomic(path, data)


def is_first_run(legacy_path):
    return not legacy_path.exists()


def _hashable(value):
    return tuple(sorted(value.items())) if isinstance(value, Mapping) else value


@dataclass(frozen=True, eq=False)
class Preferences:
    """不可變的新版設定值；以 with_changes() 取得驗證過的新副本。"""
    values: Mapping
    extra: Mapping = EMPTY     # 不認得的鍵：存成 JSON 文字，確保不可變
    schema: int = SCHEMA_VERSION
    read_only: bool = False    # True 時 save_preferences() 拒絕寫入

    def __post_init__(self):
        if set(self.values) != set(PREFERENCE_SPECS):
            raise ValueError("Preferences must define exactly the known keys")
        ordered = {key: self.values[key] for key in PREFERENCE_SPECS}
        object.__setattr__(self, "values", MappingProxyType(ordered))
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    @classmethod
    def defaults(cls, read_only=False):
        return cls({key: spec.default for key, spec in PREFERENCE_SPECS.items()},
                   read_only=read_only)

    def _identity(self):
        values = tuple((key, _hashable(value)) for key, value in self.values.items())
        return values, tuple(sorted(self.extra.items())), self.schema, self.read_only

    def __eq__(self, other):
        if not isinstance(other, Preferences):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self):
        return hash(self._identity())

    def __getitem__(self, key):
        return self.values[key]

    def __getattr__(self, name):
        # 只在一般屬性找不到時才會呼叫；避免在建構完成前遞迴。
        if name.startswith("_") or name in ("values", "extra", "schema", "read_only"):
            raise AttributeError(name)
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None

    def with_changes(self, **changes):
        """嚴格驗證：未知鍵拋出 KeyError，無效值或含無效項目拋出 ValueError。唯讀狀態保留。"""
        updated = dict(self.values)
        for key, raw in changes.items():
            spec = PREFERENCE_SPECS.get(key)
            if spec is None:
                raise KeyError("Unknown preference: " + key)
            value, dropped = spec.check(raw)
            if dropped:
                raise ValueError("Invalid entries in preference: " + key)
            updated[key] = value
        return replace(self, values=updated)

    def to_json(self):
        """轉成可寫入檔案的字典：schema、已知鍵、再接保留的未知鍵。"""
        data = {"schema": self.schema}
        data.update((key, _plain(value)) for key, value in self.values.items())
        data.update((key, json.loads(text)) for key, text in self.extra.items())
        return data


def _plain(value):
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _lenient(key, spec, raw):
    """載入時的寬鬆驗證：無效值改用預設值，部分無效項目捨棄；只記錄鍵名。"""
    try:
        value, dropped = spec.check(raw)
    except ValueError:
        LOG.warning("Invalid preference %s; using the default", key)
        return spec.default
    if dropped:
        LOG.warning("Dropped invalid entries from preference %s", key)
    return value


def _schema(raw):
    """回傳（schema, 是否唯讀）。較新版本的檔案只讀不寫，避免被降級覆寫。"""
    value = raw.get("schema", SCHEMA_VERSION)
    if type(value) is int and value > SCHEMA_VERSION:
        LOG.warning("Preferences file uses newer schema %d; saving disabled", value)
        return value, True
    if type(value) is not int or value != SCHEMA_VERSION:
        LOG.warning("Invalid preference schema; using %d", SCHEMA_VERSION)
    return SCHEMA_VERSION, False


def preferences_from_mapping(raw):
    values = {key: _lenient(key, spec, raw[key]) if key in raw else spec.default
              for key, spec in PREFERENCE_SPECS.items()}
    extra = {key: json.dumps(value) for key, value in raw.items()
             if key not in PREFERENCE_SPECS and key != "schema"}
    schema, read_only = _schema(raw)
    return Preferences(values, extra, schema, read_only)


def _decode_object(data):
    """解析為 JSON 物件；無法解碼、格式錯誤或不是物件時回傳 None。"""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) else None


def _recover_invalid(path, data, clock):
    """先把無效檔案備份成新檔，成功才允許之後覆寫；備份失敗則唯讀。"""
    try:
        backup = create_backup(path, data, int(clock()))
    except OSError as error:
        LOG.warning("Preferences file is invalid and could not be backed up (%s); "
                    "saving disabled", type(error).__name__)
        return Preferences.defaults(read_only=True)
    LOG.warning("Preferences file is invalid; copied to %s and using defaults", backup.name)
    return Preferences.defaults()


def load_preferences(path, clock=time.time):
    """讀取 preferences.json。檔案不存在時回傳預設值且不建立任何檔案。"""
    remove_stale_temporaries(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Preferences.defaults()
    except OSError as error:
        LOG.warning("Preferences file unreadable (%s); saving disabled", type(error).__name__)
        return Preferences.defaults(read_only=True)
    raw = _decode_object(data)
    if raw is None:
        return _recover_invalid(path, data, clock)
    return preferences_from_mapping(raw)


def save_preferences(path, preferences):
    """原子寫入；唯讀的設定拋出 PreferencesReadOnlyError，原檔保持不變。"""
    if preferences.read_only:
        raise PreferencesReadOnlyError("Refusing to overwrite " + path.name)
    write_json_atomic(path, preferences.to_json())


class Settings(NamedTuple):
    """啟動時載入的設定：舊版布林值、新版設定，以及是否首次啟動。"""
    legacy: Mapping
    preferences: Preferences
    first_run: bool


def load_settings(legacy_path, preferences_path):
    """不建立 settings.json 或 preferences.json；只可能清理殘留暫存檔或備份無效的 preferences.json。

    首次啟動判斷須在 initialize_preferences() 建立 settings.json 之前呼叫。
    """
    return Settings(load_legacy(legacy_path), load_preferences(preferences_path),
                    is_first_run(legacy_path))
