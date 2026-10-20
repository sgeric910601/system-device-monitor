"""使用者設定：舊版 settings.json 的五個頂端列布林值。"""
import json

DEFAULTS = dict(cpu=True, temperature=True, ram=True, gpu=True, network=True)


def save_settings(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
