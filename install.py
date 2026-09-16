#!/usr/bin/python3
"""Install this checkout for the current desktop user."""
import argparse
import os
from pathlib import Path
from runtime import (ROOT, DATA_HOME, AUTOSTART, CONFIG, desktop_entry,
                     initialize_preferences, launcher_script, set_autostart)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autostart", choices=("preserve", "on", "off"), default="preserve")
    parser.add_argument("--uninstall", action="store_true", help="Keep user preferences and source files")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run as your desktop user, without sudo.")
    launcher = Path.home()/".local/bin/system-device-monitor"
    desktop = DATA_HOME/"applications/system-device-monitor.desktop"
    if args.uninstall:
        for path in (launcher, desktop, AUTOSTART):
            path.unlink(missing_ok=True)
        print("User installation removed; preferences and source files kept.")
        return
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(launcher_script(ROOT))
    launcher.chmod(0o755)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(desktop_entry(launcher=launcher))
    initialize_preferences()
    if args.autostart != "preserve":
        set_autostart(args.autostart == "on")
    elif AUTOSTART.exists():
        set_autostart(True)
    print("Installed:", launcher)
    print("Login autostart:", "enabled" if AUTOSTART.exists() else "disabled")
    print("Preferences:", CONFIG)

if __name__ == "__main__":
    main()
