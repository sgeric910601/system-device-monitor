#!/usr/bin/python3
"""Run redacted Gitleaks checks on an explicit public export."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    scanner = os.environ.get("GITLEAKS") or shutil.which("gitleaks")
    if not scanner:
        raise SystemExit("Install gitleaks before publication (sudo apt install gitleaks).")
    with tempfile.TemporaryDirectory(prefix="sdm-public-scan-") as tmp:
        target = ROOT if args.history else args.tree
        if target is None:
            target = Path(tmp)
            names = json.loads((ROOT/"package-files.json").read_text())["source"]
            for name in names:
                source = ROOT/name
                if source.is_symlink() or ROOT not in source.resolve().parents:
                    raise SystemExit("Unsafe source path: " + name)
                output = target/name
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, output)
        command = [scanner, "detect", "--source", str(target), "--redact", "--no-banner"]
        if not args.history: command.append("--no-git")
        else: command.extend(["--log-opts=--all"])
        subprocess.run(command, check=True)

if __name__ == "__main__":
    main()
