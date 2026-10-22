#!/usr/bin/python3
"""系統與裝置監看的進入點：解析命令列、設定日誌，再啟動 GTK 看板。

--version 走快速路徑，不載入 gi，讓啟動器與套件檢查不需要圖形環境。
--dump 印出一個 JSON 自我檢查後結束，不開視窗、不寫任何檔案。
--preview 以記憶體中的示範資料開啟視窗（截圖用），不讀寫設定與歷史。
"""
import argparse
import json
import logging
from pathlib import Path
import sys

from sdm.constants import Preview
from sdm.paths import VERSION


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--output-dir", type=Path, help="Diagnostic output directory")
    parser.add_argument("--capture", action="store_true", help="Opt in to local app-window screenshots")
    parser.add_argument("--preview", choices=Preview.SCENARIOS, help="Show in-memory demo data only")
    parser.add_argument("--dump", action="store_true", help="Print a JSON self-check and exit")
    return parser


def main(argv):
    if "--version" in argv:
        print("System Device Monitor " + VERSION)
        return 0
    parser = build_parser()
    options = parser.parse_args(argv[1:])
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    if options.capture and not options.smoke_test:
        parser.error("--capture requires --smoke-test")
    if options.preview and options.capture and options.output_dir is None:
        parser.error("--preview --capture requires --output-dir")
    if options.dump:
        from sdm.services import dump
        print(json.dumps(dump.build_dump(get=dump.system_getter()), ensure_ascii=False, indent=2))
        return 0
    # 延後載入：只有真正啟動看板時才需要 GTK。
    from sdm.ui.app import run
    result = run(options)
    if result.report is not None:
        print(json.dumps(result.report), flush=True)
    return result.code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
