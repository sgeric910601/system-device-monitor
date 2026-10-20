"""登入自啟、桌面入口與啟動器腳本；不依賴 GUI。"""
from pathlib import Path
import shlex

from sdm import paths
from sdm.constants import Launcher
from sdm.core.settings import DEFAULTS, save_settings

PACKAGE_ROOT = Path("/usr/share/system-device-monitor")
PACKAGE_LAUNCHER = Path("/usr/bin/system-device-monitor")


def launcher_path():
    if paths.ROOT == PACKAGE_ROOT:
        return PACKAGE_LAUNCHER
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
        paths.AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        paths.AUTOSTART.write_text(desktop_entry(True))
    else:
        paths.AUTOSTART.unlink(missing_ok=True)


def initialize_preferences():
    # 已有設定檔代表完成過首次設定；此時沒有自啟檔就維持關閉。
    if not paths.CONFIG.exists():
        if not paths.AUTOSTART.exists():
            set_autostart(True)
        save_settings(paths.CONFIG, DEFAULTS)


def launcher_script(project):
    return """#!/bin/bash
set -eu
project=%s
for attempt in {1..%d}; do
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
""" % (shlex.quote(str(project)), Launcher.WAIT_SECONDS)
