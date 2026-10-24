#!/usr/bin/python3
"""Install this checkout for the current desktop user."""
import argparse
import os
from pathlib import Path
from sdm import paths
from sdm.core.settings import load_preferences
from sdm.services import app_icon, system_components
from sdm.services.autostart import (desktop_entry, initialize_preferences,
                                    launcher_script, set_autostart)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autostart", choices=("preserve", "on", "off"), default="preserve")
    parser.add_argument("--uninstall", action="store_true", help="Keep user preferences and source files")
    parser.add_argument("--system-components", choices=system_components.ACTIONS,
                        help="Install or remove the optional admin components: CPU power "
                             "sampling and the charge limit (asks for administrator "
                             "authentication through pkexec)")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run as your desktop user, without sudo.")
    if args.system_components:
        raise SystemExit(system_components.run(args.system_components, paths.ROOT, say=print))
    launcher = Path.home()/".local/bin/system-device-monitor"
    desktop = paths.DATA_HOME/"applications/system-device-monitor.desktop"
    if args.uninstall:
        for path in (launcher, desktop, paths.AUTOSTART):
            path.unlink(missing_ok=True)
        app_icon.remove_overrides()
        print("User installation removed; preferences and source files kept.")
        return
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(launcher_script(paths.ROOT))
    launcher.chmod(0o755)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(desktop_entry(launcher=launcher))
    color = load_preferences(paths.PREFERENCES).app_icon_color
    icon = app_icon.apply_color(color, packaged=False, keep_default=True)
    if icon.problem:
        print("Application icon not installed:", icon.problem)
    initialize_preferences()
    if args.autostart != "preserve":
        set_autostart(args.autostart == "on")
    elif paths.AUTOSTART.exists():
        set_autostart(True)
    print("Installed:", launcher)
    print("Login autostart:", "enabled" if paths.AUTOSTART.exists() else "disabled")
    print("Preferences:", paths.CONFIG)

if __name__ == "__main__":
    main()
