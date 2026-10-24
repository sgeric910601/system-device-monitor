"""dpkg-deb -c 的輸出範例（格式取自實際的輸出，大小與內容都是虛構的）。"""


def entry(kind, path, size=0):
    mode = {"d": "drwxr-xr-x", "x": "-rwxr-xr-x", "f": "-rw-r--r--"}[kind]
    return f"{mode} root/root {size:>9} 1970-01-01 00:00 ./{path}\n"


DIRECTORIES = ("", "usr/", "usr/bin/", "usr/lib/", "usr/lib/systemd/", "usr/lib/systemd/system/",
               "usr/libexec/", "usr/libexec/system-device-monitor/", "usr/share/",
               "usr/share/applications/", "usr/share/doc/", "usr/share/doc/system-device-monitor/",
               "usr/share/doc/system-device-monitor/docs/", "usr/share/icons/",
               "usr/share/icons/hicolor/", "usr/share/icons/hicolor/128x128/",
               "usr/share/icons/hicolor/128x128/apps/", "usr/share/polkit-1/",
               "usr/share/polkit-1/actions/", "usr/share/system-device-monitor/",
               "usr/share/system-device-monitor/sdm/")
FILES = (("x", "usr/bin/system-device-monitor", 512),
         ("f", "usr/lib/systemd/system/system-device-monitor-cpu-power.service", 1200),
         ("f", "usr/lib/systemd/system/system-device-monitor-cpu-power.socket", 400),
         ("x", "usr/libexec/system-device-monitor/sdm-update", 15000),
         ("f", "usr/share/applications/system-device-monitor.desktop", 300),
         ("f", "usr/share/doc/system-device-monitor/docs/USAGE.md", 9000),
         ("f", "usr/share/icons/hicolor/128x128/apps/system-device-monitor.png", 16056),
         ("f", "usr/share/polkit-1/actions/tw.local.system-device-monitor.update.policy", 900),
         ("f", "usr/share/system-device-monitor/device_monitor.py", 3000),
         ("f", "usr/share/system-device-monitor/sdm/paths.py", 1500))
LISTING = "".join(entry("d", path) for path in DIRECTORIES) + "".join(
    entry(kind, path, size) for kind, path, size in FILES)
