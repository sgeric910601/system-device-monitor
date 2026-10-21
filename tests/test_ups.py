"""不斷電系統 apcaccess 輸出解析：瓦數換算、範圍檢查與通訊中斷。"""
import dataclasses
import subprocess
import unittest

from sdm.constants import Collect, Ups
from sdm.data.ups import UpsReading, parse_ups, read_ups


def status(**fields):
    lines = [f"{key:<9}: {value}" for key, value in fields.items()]
    return "\n".join(lines) + "\n"


ONLINE = status(STATUS="ONLINE ", LOADPCT="32.0 Percent", NOMPOWER="900 Watts",
                BCHARGE="100.0 Percent", TIMELEFT="44.2 Minutes", LINEV="118.0 Volts",
                MODEL="Example UPS 1500 ")


class Parse(unittest.TestCase):
    def test_load_watts_from_percent_and_nominal_power(self):
        reading = parse_ups(ONLINE)
        self.assertEqual(reading.load_w, 288.0)
        self.assertEqual((reading.load_pct, reading.nominal_w), (32.0, 900.0))
        self.assertEqual((reading.charge_pct, reading.time_left_min, reading.line_v),
                         (100.0, 44.2, 118.0))
        self.assertTrue(reading.online)
        self.assertFalse(reading.on_battery or reading.low_battery or reading.comm_lost)
        self.assertEqual(reading.status, ("ONLINE",))
        self.assertEqual(reading.model, "Example UPS 1500")

    def test_percent_suffix_is_accepted(self):
        reading = parse_ups(status(STATUS="ONLINE", LOADPCT="17.0 Percent", NOMPOWER="900 Watts"))
        self.assertEqual(reading.load_w, 153.0)

    def test_missing_nominal_power_gives_no_watts(self):
        reading = parse_ups(status(STATUS="ONLINE", LOADPCT="32.0 Percent"))
        self.assertEqual(reading.load_pct, 32.0)
        self.assertIsNone(reading.nominal_w)
        self.assertIsNone(reading.load_w)

    def test_invalid_load_values_are_rejected(self):
        for value in ("nan", "-1", "250", "inf", "Percent", ""):
            with self.subTest(value=value):
                reading = parse_ups(status(STATUS="ONLINE", LOADPCT=value, NOMPOWER="900 Watts"))
                self.assertIsNone(reading.load_pct)
                self.assertIsNone(reading.load_w)

    def test_load_boundaries(self):
        for value, expected in (("0", 0.0), ("150", 150.0), ("150.1", None)):
            with self.subTest(value=value):
                reading = parse_ups(status(STATUS="ONLINE", LOADPCT=value))
                self.assertEqual(reading.load_pct, expected)

    def test_charge_and_nominal_ranges(self):
        reading = parse_ups(status(STATUS="ONLINE", BCHARGE="100.5 Percent", NOMPOWER="0 Watts",
                                   LOADPCT="10"))
        self.assertIsNone(reading.charge_pct)
        self.assertIsNone(reading.nominal_w)
        reading = parse_ups(status(STATUS="ONLINE", NOMPOWER="20001 Watts"))
        self.assertIsNone(reading.nominal_w)
        reading = parse_ups(status(STATUS="ONLINE", NOMPOWER="20000 Watts", BCHARGE="0"))
        self.assertEqual((reading.nominal_w, reading.charge_pct), (20000.0, 0.0))

    def test_negative_time_left_and_voltage_are_rejected(self):
        reading = parse_ups(status(STATUS="ONLINE", TIMELEFT="-3 Minutes", LINEV="-1 Volts"))
        self.assertIsNone(reading.time_left_min)
        self.assertIsNone(reading.line_v)

    def test_comm_lost_has_no_numbers(self):
        reading = parse_ups(status(STATUS="COMMLOST", LOADPCT="32.0 Percent", NOMPOWER="900 Watts",
                                   BCHARGE="100.0 Percent", MODEL="Example UPS"))
        self.assertTrue(reading.comm_lost)
        self.assertFalse(reading.online or reading.on_battery)
        numbers = (reading.charge_pct, reading.load_pct, reading.nominal_w, reading.load_w,
                   reading.time_left_min, reading.line_v)
        self.assertEqual(numbers, (None,) * 6)

    def test_on_battery_and_low_battery_flags(self):
        reading = parse_ups(status(STATUS="ONBATT LOWBATT", LOADPCT="20", NOMPOWER="900"))
        self.assertTrue(reading.on_battery)
        self.assertTrue(reading.low_battery)
        self.assertFalse(reading.online)
        self.assertEqual(reading.status, ("ONBATT", "LOWBATT"))
        self.assertEqual(reading.load_w, 180.0)

    def test_model_is_sanitized_and_capped(self):
        raw = "Back\x00UPS\t  RS\x1b[31m " + "X" * 100
        reading = parse_ups(status(STATUS="ONLINE", MODEL=raw))
        self.assertEqual(len(reading.model), Ups.MODEL_MAX_CHARS)
        self.assertTrue(reading.model.startswith("Back UPS RS [31m X"))
        self.assertTrue(reading.model.isprintable())

    def test_output_without_status_is_not_a_reading(self):
        self.assertIsNone(parse_ups(""))
        self.assertIsNone(parse_ups("Error contacting host localhost port 3551\n"))

    def test_reading_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parse_ups(ONLINE).load_w = 1


class Collect_(unittest.TestCase):
    def test_runs_apcaccess_with_the_shared_timeout(self):
        calls = []

        def run(args, timeout):
            calls.append((args, timeout))
            return ONLINE
        reading = read_ups(run)
        self.assertEqual(calls, [([Collect.APCACCESS, "status"], Collect.APCACCESS_TIMEOUT)])
        self.assertEqual(reading.load_w, 288.0)

    def test_missing_tool_or_timeout_gives_none(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("apcaccess", 2),
                      subprocess.CalledProcessError(1, "apcaccess")):
            with self.subTest(error=type(error).__name__):
                def run(args, timeout, error=error):
                    raise error
                self.assertIsNone(read_ups(run))

    def test_default_reading_has_no_values(self):
        reading = UpsReading()
        self.assertFalse(reading.online)
        self.assertIsNone(reading.load_w)


if __name__ == "__main__":
    unittest.main()
