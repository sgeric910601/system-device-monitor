#!/usr/bin/python3
"""Print one process's CPU use as a one-core percentage, including reaped children."""
import argparse
import math
import os
from pathlib import Path
import sys
import time

FIRST_TIME_FIELD = 14     # /proc/PID/stat 欄位 14–17：utime、stime、cutime、cstime
TIME_FIELDS = 4
STATE_FIELD = 3           # comm 之後的第一個欄位


def parse_cpu_ticks(text):
    """回傳欄位 14–17 的總和（時脈刻度）。

    comm 可能含空白或括號，所以從最後一個右括號之後切欄位。
    cutime／cstime 只包含已被等待回收的子程序（例如 nvidia-smi、apcaccess）。
    """
    head, separator, rest = text.rpartition(")")
    if not separator or "(" not in head:
        raise ValueError("Malformed stat line")
    start = FIRST_TIME_FIELD - STATE_FIELD
    values = rest.split()[start:start + TIME_FIELDS]
    if len(values) != TIME_FIELDS:
        raise ValueError("Missing CPU time fields")
    return sum(int(value) for value in values)


def cpu_percent(start_ticks, end_ticks, clock_ticks, wall_seconds):
    """單一核心百分比：100 代表期間內平均用滿一個核心。"""
    if wall_seconds <= 0 or clock_ticks <= 0:
        raise ValueError("Interval and clock ticks must be positive")
    if end_ticks < start_ticks:
        raise ValueError("CPU time went backwards")
    return 100.0 * (end_ticks - start_ticks) / clock_ticks / wall_seconds


def format_percent(value):
    return f"{value:.2f}"


def measure(read_ticks, seconds, clock_ticks, now=time.monotonic, sleep=time.sleep):
    """在 seconds 秒前後各讀一次 CPU 時間；時鐘與讀取函式可替換以便測試。"""
    start_ticks, start_time = read_ticks(), now()
    sleep(seconds)
    end_ticks, end_time = read_ticks(), now()
    return cpu_percent(start_ticks, end_ticks, clock_ticks, end_time - start_time)


def positive_int(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def positive_seconds(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=positive_int, help="Process ID to measure")
    parser.add_argument("seconds", type=positive_seconds, help="Measurement window in seconds")
    args = parser.parse_args(argv)
    stat = Path(f"/proc/{args.pid}/stat")
    try:
        value = measure(lambda: parse_cpu_ticks(stat.read_text()), args.seconds,
                        os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError) as error:
        print(f"Cannot measure process {args.pid}: {type(error).__name__}", file=sys.stderr)
        return 1
    print(format_percent(value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
