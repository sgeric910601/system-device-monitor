#!/usr/bin/python3
"""Check public file lists and private-data patterns without printing values."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "personal home path": re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+"),
    "private mount path": re.compile(rb"/mnt/[A-Za-z0-9_.-]+/"),
    "MAC address": re.compile(rb"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
}
EMAIL = re.compile(rb"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BLOCKED = {"settings.json", "preferences.json", "device-aliases.json", "config.json", "credentials.json",
           "dependency-status", "smoke.json"}
BLOCKED_SUFFIXES = {".png", ".jpg", ".jpeg", ".log", ".key", ".pem", ".p12", ".pfx", ".bak", ".pyc"}

def findings(path):
    issues = []
    if path.is_symlink():
        return ["symbolic link"]
    if path.name in BLOCKED or path.name.startswith(".env") or path.suffix in BLOCKED_SUFFIXES:
        issues.append("non-public file type")
    data = path.read_bytes()
    for label, regex in PATTERNS.items():
        if regex.search(data): issues.append(label)
    for email in EMAIL.findall(data):
        if not email.endswith((b"@users.noreply.github.com", b"@example.invalid")):
            issues.append("personal email")
            break
    return issues

def packaged_sources(manifest):
    """system 與 maintainer 條目引用的來源檔。"""
    system = manifest.get("system", [])
    maintainer = manifest.get("maintainer", {})
    names = [item.get("source") if isinstance(item, dict) else item
             for item in (system if isinstance(system, list) else [system])]
    names += list(maintainer.values()) if isinstance(maintainer, dict) else [maintainer]
    return names

def manifest_errors(manifest):
    """檢查清單格式 2：runtime 與打包來源都必須列在公開來源清單。"""
    errors = []
    if manifest.get("format") != 2:
        errors.append(("package-files.json", "unsupported manifest format"))
    names = set(manifest["source"])
    if not set(manifest["runtime"]) <= names:
        errors.append(("package-files.json", "runtime files missing from public source list"))
    errors.extend((str(name), "packaged file missing from public source list")
                  for name in packaged_sources(manifest) if name not in names)
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, help="Scan an unpacked publication artifact")
    args = parser.parse_args()
    errors = []
    if args.tree:
        base = args.tree.resolve()
        files = [p for p in base.rglob("*") if p.is_file() or p.is_symlink()]
    else:
        base = ROOT
        manifest = json.loads((ROOT/"package-files.json").read_text())
        names = set(manifest["source"])
        errors.extend(manifest_errors(manifest))
        files = []
        for name in sorted(names):
            p = ROOT/name
            if ROOT not in p.resolve().parents or not p.is_file():
                errors.append((name, "missing or unsafe file"))
            else:
                files.append(p)
        if (ROOT/".git").exists():
            tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
            errors.extend((name, "tracked file not in public allowlist") for name in tracked if name and name not in names)
    for path in files:
        errors.extend((str(path.relative_to(base)), finding) for finding in findings(path))
    for name, category in errors:
        print(f"FAIL: {name}: {category}")
    if errors: raise SystemExit(1)
    print(f"Privacy file check passed ({len(files)} files).")

if __name__ == "__main__":
    main()
