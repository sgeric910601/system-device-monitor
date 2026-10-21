"""CPU 用量量測腳本：/proc/PID/stat 解析與百分比計算。"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("measure_cpu", ROOT/"scripts/measure_cpu.py")
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)

# comm 為 "(a) b)"：含空白與括號，必須從最後一個右括號之後解析。
# 欄位 14–17（utime、stime、cutime、cstime）= 250、50、7、3。
STAT = ("4242 ((a) b)) S 1 4242 4242 0 -1 4194560 100 0 0 0 "
        "250 50 7 3 20 0 4 0 12345 1000 50 18446744073709551615\n")


class Parsing(unittest.TestCase):
    def test_comm_with_spaces_and_parentheses(self):
        self.assertEqual(measure.parse_cpu_ticks(STAT), 310)

    def test_malformed_lines_are_rejected(self):
        for text in ("", "4242 (a) S 1 2 3", "4242 a S " + "1 " * 20, "4242 (a) S " + "x " * 20):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    measure.parse_cpu_ticks(text)


class Percent(unittest.TestCase):
    def test_one_core_percentage(self):
        self.assertEqual(measure.cpu_percent(100, 300, 100, 2.0), 100.0)
        self.assertEqual(measure.cpu_percent(0, 25, 100, 1.0), 25.0)
        self.assertEqual(measure.format_percent(12.3456), "12.35")
        self.assertEqual(measure.format_percent(0), "0.00")

    def test_invalid_intervals_are_rejected(self):
        with self.assertRaises(ValueError):
            measure.cpu_percent(0, 10, 100, 0)
        with self.assertRaises(ValueError):
            measure.cpu_percent(20, 10, 100, 1.0)

    def test_measure_uses_injected_clock_and_reader(self):
        ticks, clock = iter((1000, 1150)), iter((10.0, 13.0))
        result = measure.measure(lambda: next(ticks), 3, clock_ticks=100,
                                 now=lambda: next(clock), sleep=lambda seconds: None)
        self.assertEqual(result, 50.0)


class CommandLine(unittest.TestCase):
    def test_own_process_prints_two_decimals(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = measure.main([str(os.getpid()), "0.05"])
        self.assertEqual(code, 0)
        text = output.getvalue().strip()
        self.assertRegex(text, r"^\d+\.\d{2}$")

    def test_invalid_arguments_exit_with_error(self):
        for argv in (["0", "1"], ["abc", "1"], ["1", "0"], ["1", "-2"]):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stop:
                        measure.main(argv)
                self.assertNotEqual(stop.exception.code, 0)

    def test_missing_process_reports_failure(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code = measure.main([str(2**22 + 1), "0.01"])
        self.assertEqual(code, 1)
        self.assertIn("process", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
