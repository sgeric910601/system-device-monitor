#!/usr/bin/python3
"""Build an allowlisted, root-owned Debian package without sudo."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdm.constants import Packaging
from sdm.paths import VERSION
from sdm.services.autostart import desktop_entry, launcher_script

MANIFEST_FORMAT = 2
APP_DIR = PurePosixPath("usr/share/system-device-monitor")
LAUNCHER = PurePosixPath("usr/bin/system-device-monitor")
DESKTOP = PurePosixPath("usr/share/applications/system-device-monitor.desktop")
DOC_DIR = PurePosixPath("usr/share/doc/system-device-monitor")
DOCS = ("README.md", "CHANGELOG.md", "docs/USAGE.md", "docs/PRIVACY.md", "docs/BUILD.md",
        "docs/SECURITY.md")
MAINTAINER_SCRIPTS = frozenset({"preinst", "postinst", "prerm", "postrm"})
SYSTEM_PREFIXES = tuple(PurePosixPath(prefix) for prefix in Packaging.SYSTEM_PREFIXES)
FILE_MODE = 0o644
DIRECTORY_MODE = 0o755
EXECUTABLE_MODE = 0o755
ALLOWED_MODE_BITS = 0o755        # 不允許 setuid／setgid／sticky 與群組或其他人可寫
MODE_PATTERN = re.compile(r"0?[0-7]{3}")
DEPENDS = ("python3 (>= 3.10)", "python3-gi", "python3-gi-cairo", "gir1.2-gtk-3.0",
           "gir1.2-gudev-1.0", "gir1.2-ayatanaappindicator3-0.1", "gir1.2-notify-0.7",
           "librsvg2-common", "util-linux", "openssl")      # openssl：驗證更新的 Ed25519 簽章


class Entry(NamedTuple):
    """套件中的一個檔案：相對於暫存根目錄的位置、來源檔與權限。"""
    target: PurePosixPath
    source: Path
    mode: int


def allowed_path(root, name):
    """清單路徑必須是專案內的一般檔案；拒絕絕對路徑、..、未正規化路徑與任何符號連結。"""
    if not isinstance(name, str) or not name:
        raise ValueError("Invalid package path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or str(relative) != name:
        raise ValueError("Invalid package path: " + name)
    path = root/name
    if path.is_symlink() or not path.is_file() or path.resolve() != root.resolve()/relative:
        raise ValueError("Invalid package path: " + name)
    return path


def public_source(manifest, root, name):
    """system 與 maintainer 的來源也必須列在公開來源清單中。"""
    if name not in manifest["source"]:
        raise ValueError("Packaged file is not a public source: " + str(name))
    return allowed_path(root, name)


def system_target(target):
    """系統檔案只能安裝到允許清單目錄之下的正規化絕對路徑。"""
    pure = PurePosixPath(target) if isinstance(target, str) and target else None
    if pure is None or not pure.is_absolute() or str(pure) != target or ".." in pure.parts:
        raise ValueError("Invalid system target: " + str(target))
    relative = pure.relative_to("/")
    if not any(prefix in relative.parents for prefix in SYSTEM_PREFIXES):
        raise ValueError("System target outside the allowed locations: " + target)
    return relative


def file_mode(text):
    """解析八進位權限字串；未指定時為 0644，且不得超出 0755。"""
    if text is None:
        return FILE_MODE
    if not isinstance(text, str) or not MODE_PATTERN.fullmatch(text):
        raise ValueError("Invalid file mode")
    mode = int(text, 8)
    if mode & ~ALLOWED_MODE_BITS or not mode & 0o400:
        raise ValueError("Unsafe file mode: " + text)
    return mode


def runtime_entries(manifest, root):
    return [Entry(APP_DIR/name, allowed_path(root, name), FILE_MODE)
            for name in manifest["runtime"]]


def system_entries(manifest, root):
    entries = []
    for item in manifest.get("system", []):
        if not isinstance(item, dict) or set(item) - {"source", "target", "mode"}:
            raise ValueError("Invalid system entry")
        entries.append(Entry(system_target(item.get("target")),
                             public_source(manifest, root, item.get("source")),
                             file_mode(item.get("mode"))))
    return entries


def maintainer_entries(manifest, root):
    scripts = manifest.get("maintainer", {})
    if not isinstance(scripts, dict):
        raise ValueError("Invalid maintainer section")
    entries = []
    for name, source in sorted(scripts.items()):
        if name not in MAINTAINER_SCRIPTS:
            raise ValueError("Unsupported maintainer script: " + str(name))
        entries.append(Entry(PurePosixPath("DEBIAN")/name,
                             public_source(manifest, root, source), EXECUTABLE_MODE))
    return entries


def doc_entries(root):
    entries = [Entry(DOC_DIR/name, allowed_path(root, name), FILE_MODE) for name in DOCS]
    return entries + [Entry(DOC_DIR/"copyright", allowed_path(root, "LICENSE"), FILE_MODE)]


def package_plan(manifest, root=ROOT):
    """驗證清單並列出所有要複製的檔案；任何問題都拋出 ValueError。"""
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError("Unsupported manifest format")
    if not isinstance(manifest.get("runtime"), list) or not isinstance(manifest.get("source"), list):
        raise ValueError("Manifest needs runtime and source lists")
    return (runtime_entries(manifest, root) + system_entries(manifest, root)
            + maintainer_entries(manifest, root) + doc_entries(root))


def control_text(version):
    return ("Package: system-device-monitor\n"
            f"Version: {version}\n"
            "Section: utils\nPriority: optional\nArchitecture: all\n"
            "Maintainer: sgeric910601 <160375598+sgeric910601@users.noreply.github.com>\n"
            "Depends: " + ", ".join(DEPENDS) + "\n"
            "Recommends: gnome-shell-extension-appindicator, upower, bluez\n"
            "Suggests: solaar, piper, ratbagd\n"
            "Homepage: https://github.com/sgeric910601/system-device-monitor\n"
            "Description: Local system resources and peripheral battery dashboard\n"
            " GTK desktop dashboard and colored AppIndicators for CPU, memory,\n"
            " GPU, network and currently connected peripherals. No telemetry.\n")


def generated_files(version):
    """由程式產生的檔案：啟動器、桌面入口與控制檔。"""
    return ((LAUNCHER, launcher_script("/" + str(APP_DIR)), EXECUTABLE_MODE),
            (DESKTOP, desktop_entry(launcher="/" + str(LAUNCHER)), FILE_MODE),
            (PurePosixPath("DEBIAN/control"), control_text(version), FILE_MODE))


def check_targets(targets):
    """每個位置只能出現一次，也不能同時是另一個檔案的上層目錄。"""
    unique = set(targets)
    if len(unique) != len(targets):
        raise ValueError("Duplicate package path")
    for target in unique:
        if any(parent in unique for parent in target.parents):
            raise ValueError("Package path nested under a file: " + str(target))


def write_tree(stage, plan, generated):
    for target, text, mode in generated:
        path = stage/target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
    for entry in plan:
        path = stage/entry.target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry.source, path)
        path.chmod(entry.mode)


def normalize_tree(stage, epoch):
    """目錄一律 0755（含根目錄），所有項目使用固定時間以利重現建置。"""
    for path in [stage, *stage.rglob("*")]:
        if path.is_dir():
            path.chmod(DIRECTORY_MODE)
    for path in sorted(stage.rglob("*"), reverse=True):
        os.utime(path, (epoch, epoch))
    os.utime(stage, (epoch, epoch))


def stage_package(manifest, stage, root=ROOT, version=VERSION, epoch=0):
    """先完整驗證再寫入；驗證失敗時不會在暫存目錄留下任何檔案。"""
    plan = package_plan(manifest, root)
    generated = generated_files(version)
    check_targets([entry.target for entry in plan] + [target for target, _, _ in generated])
    write_tree(stage, plan, generated)
    normalize_tree(stage, epoch)


def main():
    manifest = json.loads((ROOT/"package-files.json").read_text(encoding="utf-8"))
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    with tempfile.TemporaryDirectory(prefix="sdm-package-") as tmp:
        stage = Path(tmp)
        stage_package(manifest, stage, epoch=epoch)
        output = dist/f"system-device-monitor_{VERSION}_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(output)], check=True)
        print(output.name)


if __name__ == "__main__":
    main()
