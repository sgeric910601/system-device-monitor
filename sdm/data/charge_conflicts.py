"""會同時設定充電門檻的其他程式（唯讀偵測）：只提醒，仍然可以寫入。

- TLP：tlp.service 作用中（以一般權限執行 systemctl is-active，不需要授權）或 /run/tlp 存在，
  而且設定了 START_CHARGE_THRESH_* 或 STOP_CHARGE_THRESH_*。依 TLP 的讀取順序：
  /etc/tlp.d/*.conf（依檔名），最後是 /etc/tlp.conf，後面的值蓋過前面的；空值等於沒有設定。
- asusd（ASUS 筆電的 asusctl 服務）：/proc 中有名為 asusd 的程序。
- UPower 1.90.5 以後的充電門檻（GNOME 設定的「保護電池健康」就是它）：電池裝置的
  ChargeThresholdSupported 與 ChargeThresholdEnabled 都是 true；舊版沒有這兩個屬性，視為沒有。
每個衝突回傳（代號、說明），說明寫出該怎麼做。會執行 systemctl，所以在背景執行緒呼叫。
"""
from pathlib import Path
import re
import subprocess
from typing import NamedTuple

from gi.repository import GLib

from sdm.constants import ChargeLimit
from sdm.data.upower import DESTINATION, DEVICE_INTERFACE

TLP, ASUSD, UPOWER = "tlp", "asusd", "upower"
SYSTEMCTL = "/usr/bin/systemctl"
TLP_UNIT = "tlp.service"
TLP_RUN_DIR = "run/tlp"
TLP_CONF = "etc/tlp.conf"
TLP_DROP_INS = "etc/tlp.d"
CONFIG_MAX_BYTES = 65536
MAX_DROP_INS = 64
THRESHOLD_LINE = re.compile(r"\s*((?:START|STOP)_CHARGE_THRESH_[A-Za-z0-9_]+)\s*=\s*(.*)")
ASUSD_NAME = "asusd"
UPOWER_DEVICE = "/org/freedesktop/UPower/devices/battery_"
TEXTS = {
    TLP: ("TLP 正在執行，而且設定檔（/etc/tlp.conf 或 /etc/tlp.d）設定了充電門檻：它會在開機、"
          "喚醒或插拔電源時套回自己的值。請刪除或註解 START_CHARGE_THRESH／STOP_CHARGE_THRESH，"
          "或改用 TLP 調整。"),
    ASUSD: ("asusd（asusctl）正在執行，它也會設定充電上限並在開機時套回自己的值；"
            "請改用 asusctl 調整，或關閉它的充電上限。"),
    UPOWER: ("系統的「保護電池健康」（UPower 充電門檻）已開啟，它會在插拔電源或開機時套回自己的門檻；"
             "請到系統設定的「電源」關閉它，或改在那裡調整。"),
}


class Conflict(NamedTuple):
    kind: str
    text: str


def run_quiet(argv, timeout):
    """一般權限、參數清單、不經過 shell；回傳結束碼。"""
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=False).returncode


def threshold_value(raw):
    """設定值：有引號時取引號內，否則取到第一個空白或 #。"""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        return raw[1:].split(raw[0], 1)[0]
    return re.split(r"[\s#]", raw, maxsplit=1)[0]


def read_config(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(CONFIG_MAX_BYTES)
    except OSError:
        return ""


def tlp_files(root):
    try:
        drop_ins = sorted(path for path in (Path(root)/TLP_DROP_INS).iterdir()
                          if path.suffix == ".conf")[:MAX_DROP_INS]
    except OSError:
        drop_ins = []
    return drop_ins + [Path(root)/TLP_CONF]


def tlp_thresholds_set(root=Path("/")):
    """TLP 最後採用的設定中，是否有任何一個充電門檻不是空的。"""
    values = {}
    for path in tlp_files(root):
        for line in read_config(path).splitlines():
            match = THRESHOLD_LINE.fullmatch(line.rstrip("\r"))
            if match:
                values[match.group(1)] = threshold_value(match.group(2))
    return any(values.values())


def tlp_running(root, run):
    if (Path(root)/TLP_RUN_DIR).is_dir():
        return True
    try:
        return run([SYSTEMCTL, "is-active", "--quiet", TLP_UNIT],
                   ChargeLimit.CONFLICT_TIMEOUT) == 0
    except (OSError, subprocess.SubprocessError):
        return False


def process_running(proc, name):
    try:
        entries = list(Path(proc).iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.name.isdigit():
            try:
                if (entry/"comm").read_text(errors="replace").strip() == name:
                    return True
            except OSError:
                continue
    return False


def upower_thresholds_enabled(get, supply):
    """UPower 的充電門檻已開啟；舊版 UPower 或讀不到時為 False。"""
    path = UPOWER_DEVICE + supply
    try:
        supported = get(DESTINATION, path, DEVICE_INTERFACE, "ChargeThresholdSupported")
        enabled = get(DESTINATION, path, DEVICE_INTERFACE, "ChargeThresholdEnabled")
    except GLib.Error:
        return False
    return supported is True and enabled is True


def detect_conflicts(supply, root=Path("/"), proc=Path("/proc"), run=run_quiet, upower=None):
    """（Conflict, …）依 TLP、asusd、UPower 的順序；upower(電池) 回傳 UPower 的門檻是否開啟。"""
    found = []
    if tlp_thresholds_set(root) and tlp_running(root, run):
        found.append(TLP)
    if process_running(proc, ASUSD_NAME):
        found.append(ASUSD)
    if upower is not None and upower(supply):
        found.append(UPOWER)
    return tuple(Conflict(kind, TEXTS[kind]) for kind in found)
