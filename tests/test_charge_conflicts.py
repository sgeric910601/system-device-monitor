"""會同時設定充電門檻的其他程式：TLP 設定的各種寫法、asusd 與 UPower 的充電門檻。"""
from pathlib import Path
import tempfile
import unittest

from gi.repository import GLib

from sdm.data.charge_conflicts import (ASUSD, TLP, UPOWER, detect_conflicts, tlp_thresholds_set,
                                       upower_thresholds_enabled)


class TlpConfig(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/"etc/tlp.d").mkdir(parents=True)

    def conf(self, text, name=None):
        path = self.root/"etc"/"tlp.conf" if name is None else self.root/"etc/tlp.d"/name
        path.write_text(text)

    def test_variants_that_set_a_threshold(self):
        for text in ("STOP_CHARGE_THRESH_BAT0=80\n", "START_CHARGE_THRESH_BAT0=75\n",
                     'STOP_CHARGE_THRESH_BAT1="80"\n', "  STOP_CHARGE_THRESH_BAT0 = 80\n",
                     "STOP_CHARGE_THRESH_BAT0=80 # 我的設定\n", "STOP_CHARGE_THRESH_CMB0=1\n",
                     "# comment\nSTOP_CHARGE_THRESH_BAT0=80\r\n"):
            with self.subTest(text=text):
                self.conf(text)
                self.assertTrue(tlp_thresholds_set(self.root))

    def test_variants_that_do_not(self):
        for text in ("#STOP_CHARGE_THRESH_BAT0=80\n", "# STOP_CHARGE_THRESH_BAT0=80\n",
                     'STOP_CHARGE_THRESH_BAT0=""\n', "STOP_CHARGE_THRESH_BAT0=\n",
                     "TLP_ENABLE=1\nCPU_SCALING_GOVERNOR_ON_AC=performance\n",
                     "XSTOP_CHARGE_THRESH_BAT0=80\n", ""):
            with self.subTest(text=text):
                self.conf(text)
                self.assertFalse(tlp_thresholds_set(self.root))

    def test_drop_ins_count_and_tlp_conf_overrides_them(self):
        self.conf("STOP_CHARGE_THRESH_BAT0=80\n", "50-battery.conf")
        self.assertTrue(tlp_thresholds_set(self.root))
        self.conf('STOP_CHARGE_THRESH_BAT0=""\n')
        self.assertFalse(tlp_thresholds_set(self.root))

    def test_later_drop_ins_override_earlier_ones(self):
        self.conf("START_CHARGE_THRESH_BAT0=75\n", "10-a.conf")
        self.conf("START_CHARGE_THRESH_BAT0=\n", "20-b.conf")
        self.assertFalse(tlp_thresholds_set(self.root))

    def test_only_conf_files_in_the_drop_in_directory(self):
        self.conf("STOP_CHARGE_THRESH_BAT0=80\n", "battery.conf.bak")
        self.assertFalse(tlp_thresholds_set(self.root))

    def test_missing_configuration(self):
        self.assertFalse(tlp_thresholds_set(self.root/"nowhere"))


class FakeProc:
    def __init__(self, case, names):
        temp = tempfile.TemporaryDirectory()
        case.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        for pid, name in enumerate(names, start=100):
            (self.path/str(pid)).mkdir()
            (self.path/str(pid)/"comm").write_text(name + "\n")
        (self.path/"self").mkdir()


class Detect(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/"etc").mkdir()
        self.calls = []

    def run_command(self, active=()):
        def run(argv, timeout):
            self.calls.append((tuple(argv), timeout))
            return 0 if argv[-1] in active else 3
        return run

    def detect(self, proc_names=(), active=(), upower=None):
        proc = FakeProc(self, proc_names)
        return detect_conflicts("BAT0", root=self.root, proc=proc.path,
                                run=self.run_command(active), upower=upower)

    def test_nothing_running(self):
        self.assertEqual(self.detect(), ())

    def test_tlp_running_with_thresholds(self):
        (self.root/"etc/tlp.conf").write_text("STOP_CHARGE_THRESH_BAT0=80\n")
        conflicts = self.detect(active=("tlp.service",))
        self.assertEqual([item.kind for item in conflicts], [TLP])
        self.assertIn("/etc/tlp.conf", conflicts[0].text)
        self.assertEqual(self.calls, [(("/usr/bin/systemctl", "is-active", "--quiet",
                                        "tlp.service"), 3)])

    def test_tlp_run_directory_counts_as_running(self):
        (self.root/"etc/tlp.conf").write_text("STOP_CHARGE_THRESH_BAT0=80\n")
        (self.root/"run/tlp").mkdir(parents=True)
        self.assertEqual([item.kind for item in self.detect()], [TLP])

    def test_tlp_without_thresholds_or_not_running_is_fine(self):
        (self.root/"etc/tlp.conf").write_text("TLP_ENABLE=1\n")
        self.assertEqual(self.detect(active=("tlp.service",)), ())
        (self.root/"etc/tlp.conf").write_text("STOP_CHARGE_THRESH_BAT0=80\n")
        self.assertEqual(self.detect(), ())

    def test_asusd(self):
        conflicts = self.detect(proc_names=("bash", "asusd", "gnome-shell"))
        self.assertEqual([item.kind for item in conflicts], [ASUSD])
        self.assertEqual(self.detect(proc_names=("asusd-user", "xasusd")), ())

    def test_upower_threshold(self):
        conflicts = self.detect(upower=lambda supply: True)
        self.assertEqual([item.kind for item in conflicts], [UPOWER])
        self.assertEqual(self.detect(upower=lambda supply: False), ())

    def test_every_conflict_says_what_to_do(self):
        (self.root/"etc/tlp.conf").write_text("STOP_CHARGE_THRESH_BAT0=80\n")
        conflicts = self.detect(proc_names=("asusd",), active=("tlp.service",),
                                upower=lambda supply: True)
        self.assertEqual([item.kind for item in conflicts], [TLP, ASUSD, UPOWER])
        for item in conflicts:
            self.assertTrue(item.text.endswith("。"))

    def test_failed_systemctl_is_not_running(self):
        (self.root/"etc/tlp.conf").write_text("STOP_CHARGE_THRESH_BAT0=80\n")

        def broken(argv, timeout):
            raise OSError("missing")
        proc = FakeProc(self, ())
        self.assertEqual(detect_conflicts("BAT0", root=self.root, proc=proc.path, run=broken), ())


class UpowerProperties(unittest.TestCase):
    def getter(self, values):
        self.paths = []

        def get(destination, path, interface, name):
            self.paths.append((destination, path, interface, name))
            if name not in values:
                raise GLib.Error("No such property")
            return values[name]
        return get

    def test_enabled_needs_both_properties(self):
        get = self.getter({"ChargeThresholdSupported": True, "ChargeThresholdEnabled": True})
        self.assertTrue(upower_thresholds_enabled(get, "BAT0"))
        self.assertEqual(self.paths[0][:3], ("org.freedesktop.UPower",
                                             "/org/freedesktop/UPower/devices/battery_BAT0",
                                             "org.freedesktop.UPower.Device"))
        get = self.getter({"ChargeThresholdSupported": True, "ChargeThresholdEnabled": False})
        self.assertFalse(upower_thresholds_enabled(get, "BAT0"))
        get = self.getter({"ChargeThresholdSupported": False, "ChargeThresholdEnabled": True})
        self.assertFalse(upower_thresholds_enabled(get, "BAT0"))

    def test_older_upower_without_the_properties(self):
        self.assertFalse(upower_thresholds_enabled(self.getter({}), "BAT0"))

    def test_values_must_be_booleans(self):
        get = self.getter({"ChargeThresholdSupported": 1, "ChargeThresholdEnabled": "true"})
        self.assertFalse(upower_thresholds_enabled(get, "BAT0"))


if __name__ == "__main__":
    unittest.main()
