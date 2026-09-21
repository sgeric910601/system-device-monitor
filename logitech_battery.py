"""Optional Solaar CLI adapter; retain only battery data, never raw output."""
import os
from pathlib import Path
import re
import subprocess
import threading
import time

INTERVAL = 60
TIMEOUT = 15
MAX_AGE = 90


def parse_solaar_show(text):
    """Parse G304/G305 battery-status reports, whose percentages are coarse."""
    devices, current = [], None
    for line in text.splitlines():
        if line and not line[0].isspace():
            current = None
        if re.match(r"^  \d+: .+$", line):
            current = {}
            devices.append(current)
        elif current is not None:
            if match := re.match(r"^     Device path\s*:\s*(/dev/hidraw\d+)\s*$", line):
                current["path"] = match[1]
            elif match := re.match(r"^     WPID\s*:\s*([0-9a-fA-F]+)\s*$", line):
                current["wpid"] = match[1].lower()
            elif match := re.match(r"^     USB id\s*:\s*046d:([0-9a-fA-F]+)\s*$", line):
                current["wpid"] = match[1].lower()
            elif line.startswith("     Protocol"):
                current["online"] = "HID++" in line
            elif re.search(r"\bBATTERY STATUS\s+\{1000\}", line):
                current["coarse"] = True
            elif line.startswith("     Battery:"):
                # Only the final device summary, not a nested feature report.
                current.pop("percent", None)
                current.pop("next", None)
                match = re.fullmatch(
                    r"     Battery: (\d+)%, [^,\n]+(?:, next level (\d+)%)[.]\s*", line)
                if match and 0 < int(match[1]) <= 100 and 0 <= int(match[2]) <= 100:
                    current["percent"], current["next"] = int(match[1]), int(match[2])
            elif line == "     Battery status unavailable.":
                current.pop("percent", None)
    return {d["path"]: {k: d[k] for k in ("percent", "next")}
            for d in devices if d.get("path") and d.get("wpid") == "4074"
            and d.get("online") and d.get("coarse") and "percent" in d}


def inventory():
    """Identify the paired HID device, its physical receiver, and online state."""
    result = {}
    for node in Path("/sys/class/hidraw").glob("hidraw*"):
        device = node / "device"
        try:
            if "HID_ID=0003:0000046D:00004074" not in (device / "uevent").read_text().splitlines():
                continue
            parent = next(p for p in device.resolve().parents if (p / "idVendor").exists())
            if (parent / "idVendor").read_text().strip() != "046d":
                continue
            online = any((p / "online").read_text().strip() == "1"
                         for p in (device / "power_supply").glob("*") if (p / "online").exists())
            result[str(parent)] = (
                str(Path("/dev") / node.name), str(device.resolve()),
                (parent / "devnum").read_text().strip(), online)
        except (OSError, StopIteration, UnicodeError):
            continue
    return result


class SolaarBattery:
    def __init__(self):
        self.lock = threading.Lock()
        self.observed = {}
        self.epochs = {}
        self.records = {}

    def observe(self):
        current = inventory()
        with self.lock:
            for parent in self.observed.keys() | current.keys():
                if self.observed.get(parent) != current.get(parent):
                    self.epochs[parent] = self.epochs.get(parent, 0) + 1
                    self.records.pop(parent, None)
            self.observed = current
            return current.copy(), self.epochs.copy()

    def refresh(self):
        before, epochs = self.observe()
        active = {parent: item for parent, item in before.items() if item[-1]}
        if not active:
            return {"available": False}
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        env.update(LC_ALL="C", PYTHONNOUSERSITE="1")
        try:
            output = subprocess.run(
                ["/usr/bin/solaar", "show"], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT, check=True, env=env).stdout
            parsed = parse_solaar_show(output)
        except (OSError, subprocess.SubprocessError):
            with self.lock:
                self.records.clear()
            return {"available": False}
        after, _ = self.observe()
        now, stamp = time.monotonic(), time.time()
        with self.lock:
            self.records = {
                parent: {**parsed[item[0]], "stamp": stamp, "monotonic": now}
                for parent, item in active.items()
                if before[parent] == after.get(parent)
                and epochs.get(parent) == self.epochs.get(parent)
                and item[0] in parsed
            }
            return {"available": bool(self.records)}

    def sample(self, parent):
        with self.lock:
            record = self.records.get(parent)
            if (record and 0 <= time.monotonic() - record["monotonic"] <= MAX_AGE
                    and 0 <= time.time() - record["stamp"] <= MAX_AGE):
                return record.copy()
        return None


def apply_battery(device, sample):
    if sample is None:
        return
    value = sample["percent"]
    device.update(battery=f"約 {value}%（分段回報）", percent=value,
                  stamp=sample["stamp"], source="Solaar / HID++",
                  detail=f"裝置分段回報 · 下一級 {sample['next']}%")
