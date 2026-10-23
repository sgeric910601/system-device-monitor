"""測試用的私人工作階段匯流排設定：不提供任何可自動啟動的服務（通知、桌面入口、鑰匙圈等），
只留下程式本身的行為；以 dbus-run-session --config-file 使用。"""
import os
from pathlib import Path
import shutil

DBUS_RUN_SESSION = shutil.which("dbus-run-session")
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:abstract={name}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""
REMOVED = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")


def write_config(directory, prefix):
    """在 directory 寫入匯流排設定；回傳（設定檔路徑, 匯流排位址的開頭）。"""
    name = f"{prefix}{os.getpid()}"
    path = Path(directory)/"bus.conf"
    path.write_text(BUS_CONFIG.format(name=name))
    return path, "unix:abstract=" + name


def clean_environment(**extra):
    """去掉顯示器與使用者匯流排的環境變數，避免連到真正的桌面。"""
    env = {key: value for key, value in os.environ.items() if key not in REMOVED}
    env.update(extra)
    return env
