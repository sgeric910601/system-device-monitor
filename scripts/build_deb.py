#!/usr/bin/python3
"""Build an allowlisted, root-owned Debian package without sudo."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import VERSION, desktop_entry, launcher_script

def allowed_path(name):
    path = ROOT / name
    if path.is_symlink() or not path.is_file() or ROOT not in path.resolve().parents:
        raise ValueError("Invalid package path: " + name)
    return path

def main():
    manifest = json.loads((ROOT/"package-files.json").read_text())
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sdm-package-") as tmp:
        stage = Path(tmp)
        appdir = stage/"usr/share/system-device-monitor"
        for name in manifest["runtime"]:
            target = appdir/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(allowed_path(name), target)
            target.chmod(0o644)
        launcher = stage/"usr/bin/system-device-monitor"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(launcher_script("/usr/share/system-device-monitor"))
        launcher.chmod(0o755)
        desktop = stage/"usr/share/applications/system-device-monitor.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(desktop_entry(launcher="/usr/bin/system-device-monitor"))
        docs = stage/"usr/share/doc/system-device-monitor"
        docs.mkdir(parents=True, exist_ok=True)
        for name in ("README.md", "CHANGELOG.md", "docs/USAGE.md", "docs/PRIVACY.md", "docs/BUILD.md"):
            target = docs/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(allowed_path(name), target)
        shutil.copyfile(allowed_path("LICENSE"), docs/"copyright")
        control = stage/"DEBIAN/control"
        control.parent.mkdir(parents=True)
        control.write_text(
            "Package: system-device-monitor\n"
            f"Version: {VERSION}\n"
            "Section: utils\nPriority: optional\nArchitecture: all\n"
            "Maintainer: sgeric910601 <160375598+sgeric910601@users.noreply.github.com>\n"
            "Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, "
            "gir1.2-gudev-1.0, gir1.2-ayatanaappindicator3-0.1, librsvg2-common, util-linux\n"
            "Recommends: gnome-shell-extension-appindicator, upower, bluez\n"
            "Homepage: https://github.com/sgeric910601/system-device-monitor\n"
            "Description: Local system resources and peripheral battery dashboard\n"
            " GTK desktop dashboard and colored AppIndicators for CPU, memory,\n"
            " GPU, network and currently connected peripherals. No telemetry.\n")
        for path in stage.rglob("*"):
            if path.is_dir(): path.chmod(0o755)
            elif path != launcher: path.chmod(0o644)
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        for path in sorted(stage.rglob("*"), reverse=True):
            os.utime(path, (epoch, epoch))
        os.utime(stage, (epoch, epoch))
        output = dist/f"system-device-monitor_{VERSION}_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(output)], check=True)
        print(output.name)

if __name__ == "__main__":
    main()
