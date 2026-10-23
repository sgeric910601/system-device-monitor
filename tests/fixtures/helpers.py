"""載入沒有副檔名的管理員小程式（helper/ 底下）與 scripts/ 的獨立腳本，讓測試直接呼叫函式。

小程式以 root 執行時不載入專案裡的任何模組，所以測試也不經由 sdm 套件，而是依檔案路徑載入；
模組名稱把「-」換成「_」，避免與 sys.modules 裡的其他模組衝突。
"""
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPERS = ROOT/"helper"


def load_path(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_helper(file_name):
    """helper/ 底下的小程式，例如 sdm-cpu-power-service。"""
    return load_path(HELPERS/file_name, "helper_" + file_name.replace("-", "_"))


def load_script(name):
    """scripts/ 底下的腳本（不含 .py）。"""
    return load_path(ROOT/"scripts"/(name + ".py"), "script_" + name)
