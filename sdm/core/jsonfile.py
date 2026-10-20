"""JSON 檔案讀取；讀不到或格式不符時回傳空字典。"""
import json


def load_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
