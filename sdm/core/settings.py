"""使用者設定：舊版 settings.json 與新版型別化 preferences.json。

相容規則：
- settings.json 維持舊格式，只存五個頂端列布林值；舊版與退回舊版都只讀寫它。
- 新設定存在同目錄的 preferences.json（{"schema": 2, ...}），舊版不會讀取或覆寫。
- 首次啟動只看 settings.json 是否存在；缺少 preferences.json 不算首次啟動。
- preferences.json 中不認得的鍵在儲存時原樣保留；無效值改用預設值並記錄鍵名。
"""
from dataclasses import dataclass, replace
import json
import logging
from types import MappingProxyType
from typing import Mapping, NamedTuple

from sdm.core.jsonfile import load_json, write_json_atomic
from sdm.core.preference_specs import PREFERENCE_SPECS, SCHEMA_VERSION

LOG = logging.getLogger(__name__)

LEGACY_KEYS = ("cpu", "temperature", "ram", "gpu", "network")
LEGACY_DEFAULTS = MappingProxyType({key: True for key in LEGACY_KEYS})
EMPTY = MappingProxyType({})


def load_legacy(path):
    """讀取舊版五個布林值；缺少或型別不符的值沿用預設 True。"""
    stored = load_json(path)
    return MappingProxyType({key: stored[key] if isinstance(stored.get(key), bool) else default
                             for key, default in LEGACY_DEFAULTS.items()})


def save_legacy(path, values):
    """只寫回舊版格式的五個布林值，與舊版寫出的內容完全相同。"""
    data = {}
    for key in LEGACY_KEYS:
        if type(values.get(key)) is not bool:
            raise ValueError("Legacy setting must be a boolean: " + key)
        data[key] = values[key]
    write_json_atomic(path, data)


def is_first_run(legacy_path):
    return not legacy_path.exists()


@dataclass(frozen=True)
class Preferences:
    """不可變的新版設定值；以 with_changes() 取得驗證過的新副本。"""
    values: Mapping
    extra: Mapping = EMPTY     # 不認得的鍵：存成 JSON 文字，確保不可變
    schema: int = SCHEMA_VERSION

    def __post_init__(self):
        if set(self.values) != set(PREFERENCE_SPECS):
            raise ValueError("Preferences must define exactly the known keys")
        ordered = {key: self.values[key] for key in PREFERENCE_SPECS}
        object.__setattr__(self, "values", MappingProxyType(ordered))
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    @classmethod
    def defaults(cls):
        return cls({key: spec.default for key, spec in PREFERENCE_SPECS.items()})

    def __getitem__(self, key):
        return self.values[key]

    def __getattr__(self, name):
        # 只在一般屬性找不到時才會呼叫；避免在建構完成前遞迴。
        if name.startswith("_") or name in ("values", "extra", "schema"):
            raise AttributeError(name)
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None

    def with_changes(self, **changes):
        """嚴格驗證：未知鍵拋出 KeyError，無效值或含無效項目拋出 ValueError。"""
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
    value = raw.get("schema", SCHEMA_VERSION)
    if type(value) is int and value >= SCHEMA_VERSION:
        return value
    LOG.warning("Invalid preference schema; using %d", SCHEMA_VERSION)
    return SCHEMA_VERSION


def preferences_from_mapping(raw):
    values = {key: _lenient(key, spec, raw[key]) if key in raw else spec.default
              for key, spec in PREFERENCE_SPECS.items()}
    extra = {key: json.dumps(value) for key, value in raw.items()
             if key not in PREFERENCE_SPECS and key != "schema"}
    return Preferences(values, extra, _schema(raw))


def load_preferences(path):
    """讀取 preferences.json；檔案不存在時回傳預設值，不寫入任何檔案。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Preferences.defaults()
    except (OSError, ValueError):
        LOG.warning("Preferences file unreadable; using defaults")
        return Preferences.defaults()
    if not isinstance(raw, dict):
        LOG.warning("Preferences file is not an object; using defaults")
        return Preferences.defaults()
    return preferences_from_mapping(raw)


def save_preferences(path, preferences):
    write_json_atomic(path, preferences.to_json())


class Settings(NamedTuple):
    """啟動時載入的設定：舊版布林值、新版設定，以及是否首次啟動。"""
    legacy: Mapping
    preferences: Preferences
    first_run: bool


def load_settings(legacy_path, preferences_path):
    """只讀取，不建立任何檔案；首次啟動判斷須在建立 settings.json 之前呼叫。"""
    return Settings(load_legacy(legacy_path), load_preferences(preferences_path),
                    is_first_run(legacy_path))
