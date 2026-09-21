"""Shared installation paths and user preferences; no GUI dependencies."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text().strip()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home()/".config")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or Path.home()/".local/share")
CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or Path.home()/".cache")
CONFIG = CONFIG_HOME / "system-device-monitor/settings.json"
ALIASES = CONFIG.parent / "device-aliases.json"
AUTOSTART = CONFIG_HOME / "autostart/system-device-monitor.desktop"
CACHE = CACHE_HOME / "system-device-monitor"
DEFAULTS = dict(cpu=True, temperature=True, ram=True, gpu=True, network=True)

def load_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def save_settings(settings):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(CONFIG)

def launcher_path():
    if ROOT == Path("/usr/share/system-device-monitor"):
        return Path("/usr/bin/system-device-monitor")
    return Path.home()/".local/bin/system-device-monitor"

def desktop_entry(background=False, launcher=None):
    value = str(launcher or launcher_path()).replace("%", "%%")
    for char in ("\\", '"', chr(96), "$"):
        value = value.replace(char, "\\" + char)
    value = value.replace("\\", "\\\\")
    return ("[Desktop Entry]\nType=Application\nName=系統與裝置監看\n"
            "Name[en]=System Device Monitor\n"
            "Comment=即時查看系統資源與周邊設備電量\n"
            f'Exec=/usr/bin/env "{value}"' + (" --background" if background else "") +
            "\nIcon=utilities-system-monitor\nTerminal=false\nCategories=System;Monitor;\n"
            "StartupWMClass=tw.local.SystemDeviceMonitor\n"
            "X-GNOME-Autostart-enabled=true\n")

def set_autostart(enabled):
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(desktop_entry(True))
    else:
        AUTOSTART.unlink(missing_ok=True)

def initialize_preferences():
    # Existing preferences mark first-run setup; absent autostart then stays off.
    if not CONFIG.exists():
        if not AUTOSTART.exists():
            set_autostart(True)
        save_settings(DEFAULTS)

def launcher_script(project):
    import shlex
    return """#!/bin/bash
set -eu
project=%s
for attempt in {1..60}; do
    if [ -f "$project/device_monitor.py" ]; then
        cache="${XDG_CACHE_HOME:-$HOME/.cache}/system-device-monitor"
        mkdir -p "$cache"
        if [[ " $* " == *" --version "* || " $* " == *" --smoke-test "* ]]; then
            exec /usr/bin/python3 -B "$project/device_monitor.py" "$@"
        fi
        exec /usr/bin/python3 -B "$project/device_monitor.py" "$@" >>"$cache/app.log" 2>&1
    fi
    sleep 1
done
echo "System Device Monitor: application files unavailable" >&2
exit 1
""" % shlex.quote(str(project))
