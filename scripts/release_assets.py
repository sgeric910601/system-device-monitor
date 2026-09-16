#!/usr/bin/python3
"""Create source archives from a clean Git HEAD, plus SHA256 checksums."""
import argparse
import gzip
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import VERSION

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT):
        raise SystemExit("Commit tracked changes before creating release assets.")
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    name = f"system-device-monitor-{VERSION}"
    archive = dist/(name+".tar.gz")
    content = subprocess.check_output(["git", "archive", "--format=tar", "--prefix="+name+"/", "HEAD"], cwd=ROOT)
    archive.write_bytes(gzip.compress(content, mtime=0))
    outputs = [archive]
    if not args.source_only:
        package = dist/f"system-device-monitor_{VERSION}_all.deb"
        if not package.is_file(): raise SystemExit("Run make deb first.")
        outputs.append(package)
    (dist/"SHA256SUMS").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.name+"\n" for path in outputs))

if __name__ == "__main__":
    main()
