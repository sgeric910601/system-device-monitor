"""AMD APU 的 PPT 校正：與 RAPL 比對至少 30 筆，中位數比例在 ±25% 內才確認可用。

比例＝（ppt_raw ÷ 1000）÷ RAPL package 瓦數；測試用合成的數列，不用實機資料。
"""
import json
import math
from pathlib import Path
import tempfile
import unittest

from sdm.constants import CpuPower
from sdm.core.ppt_calibration import (PptCalibration, load_state, parse_state, save_state,
                                      state_value)

SAMPLES = CpuPower.CALIBRATION_SAMPLES


class Recorder:
    def __init__(self):
        self.saved = []

    def __call__(self, value):
        self.saved.append(value)


def feed(calibration, ratios, rapl=40.0):
    for ratio in ratios:
        calibration.observe(rapl * ratio * CpuPower.PPT_SCALE, rapl)


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.save = Recorder()
        self.calibration = PptCalibration(save=self.save)

    def test_needs_thirty_samples(self):
        feed(self.calibration, [1.0] * (SAMPLES - 1))
        self.assertFalse(self.calibration.verified)
        feed(self.calibration, [1.0])
        self.assertTrue(self.calibration.verified)
        self.assertEqual(self.save.saved, [{"format": 1, "ppt": {"scale": 1000,
                                                                 "verified": True}}])

    def test_median_within_a_quarter_is_accepted(self):
        for ratio in (0.76, 0.9, 1.1, 1.24):
            with self.subTest(ratio=ratio):
                calibration = PptCalibration()
                feed(calibration, [ratio] * SAMPLES)
                self.assertTrue(calibration.verified)

    def test_median_outside_a_quarter_is_rejected(self):
        for ratio in (0.001, 0.5, 0.74, 1.26, 1000.0):
            with self.subTest(ratio=ratio):
                save = Recorder()
                calibration = PptCalibration(save=save)
                feed(calibration, [ratio] * SAMPLES)
                self.assertFalse(calibration.verified)
                self.assertEqual(save.saved, [])

    def test_outliers_do_not_move_the_median(self):
        ratios = [1.05] * 20 + [50.0] * 5 + [0.01] * 5
        feed(self.calibration, ratios)
        self.assertTrue(self.calibration.verified)

    def test_varying_load_is_compared_sample_by_sample(self):
        for index in range(SAMPLES):
            rapl = 10.0 + index * 3
            self.calibration.observe(rapl * 1.08 * 1000, rapl)
        self.assertTrue(self.calibration.verified)

    def test_later_disagreement_revokes_the_calibration(self):
        feed(self.calibration, [1.0] * SAMPLES)
        feed(self.calibration, [2.0] * SAMPLES)
        self.assertFalse(self.calibration.verified)
        self.assertEqual([value["ppt"]["verified"] for value in self.save.saved], [True, False])

    def test_invalid_pairs_are_ignored(self):
        for ppt, rapl in ((math.nan, 40.0), (-1.0, 40.0), (40000.0, math.nan), (40000.0, 0.5),
                          (40000.0, -3.0), (None, 40.0), (40000.0, None), (True, 40.0),
                          (40000.0, CpuPower.CALIBRATION_MAX_W)):
            self.calibration.observe(ppt, rapl)
        feed(self.calibration, [1.0] * (SAMPLES - 1))
        self.assertFalse(self.calibration.verified)


class Watts(unittest.TestCase):
    def test_only_verified_calibration_gives_watts(self):
        calibration = PptCalibration()
        self.assertIsNone(calibration.watts(20000.0))
        feed(calibration, [1.0] * SAMPLES)
        self.assertEqual(calibration.watts(20000.0), 20.0)
        self.assertEqual(calibration.watts(41040.0), 41.0)
        for raw in (None, math.nan, -5.0, CpuPower.CALIBRATION_MAX_W * 1000.0, "1"):
            self.assertIsNone(calibration.watts(raw))

    def test_loaded_state(self):
        verified = {"format": 1, "ppt": {"scale": 1000, "verified": True}}
        self.assertEqual(PptCalibration(load=lambda: verified).watts(10000.0), 10.0)
        for value in (None, {}, [], {"format": 2, "ppt": {"scale": 1000, "verified": True}},
                      {"format": 1, "ppt": {"scale": 1, "verified": True}},
                      {"format": 1, "ppt": {"scale": 1000, "verified": "yes"}},
                      {"format": 1, "ppt": "verified"}):
            with self.subTest(value=value):
                self.assertFalse(PptCalibration(load=lambda value=value: value).verified)

    def test_state_round_trip(self):
        self.assertTrue(parse_state(state_value(True)).verified)
        self.assertFalse(parse_state(state_value(False)).verified)


class Storage(unittest.TestCase):
    def test_save_and_load_a_state_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"state"/"cpu-calibration.json"
            self.assertEqual(load_state(path), {})
            save_state(path, state_value(True))
            self.assertEqual(json.loads(path.read_text()), state_value(True))
            self.assertTrue(PptCalibration(load=lambda: load_state(path)).verified)

    def test_unwritable_location_is_only_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            blocker = Path(temp)/"file"
            blocker.write_text("x")
            with self.assertLogs("sdm.core.ppt_calibration", "WARNING"):
                save_state(blocker/"cpu-calibration.json", state_value(True))

    def test_broken_file_reads_as_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"cpu-calibration.json"
            path.write_text("{broken")
            self.assertEqual(load_state(path), {})


if __name__ == "__main__":
    unittest.main()
