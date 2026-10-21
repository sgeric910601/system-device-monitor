#!/usr/bin/python3
"""Import every module under ROOT/sdm, e.g. from an unpacked .deb, and list failures."""
import argparse
import importlib
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def module_names(root):
    """依檔案樹列出 sdm 底下所有模組名稱；__init__.py 對應套件本身。"""
    names = []
    for path in (root/"sdm").rglob("*.py"):
        parts = path.relative_to(root).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return sorted(names)


def import_all(root, names):
    """逐一匯入並收集失敗，不在第一個錯誤就停止；也確認模組確實來自 root。"""
    failures = []
    for name in names:
        try:
            module = importlib.import_module(name)
        except Exception as error:
            failures.append(f"{name}: {type(error).__name__}: {error}")
            continue
        origin = Path(module.__file__).resolve()
        if root not in origin.parents:
            failures.append(f"{name}: imported from outside the root")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT,
                        help="Directory that contains the sdm package")
    root = parser.parse_args(argv).root.resolve()
    names = module_names(root)
    if not names:
        print(f"FAIL: no modules found under {root/'sdm'}")
        return 1
    sys.path.insert(0, str(root))
    failures = import_all(root, names)
    for line in failures:
        print("FAIL: " + line)
    if failures:
        return 1
    print(f"Imported {len(names)} modules from {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
