"""CI 安裝 .deb 後必須實際檢查管理員元件；避免之後的修改不小心拿掉這些檢查。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT/".github/workflows/ci.yml").read_text(encoding="utf-8")
HELPER = "/usr/libexec/system-device-monitor/sdm-cpu-power"


class Workflow(unittest.TestCase):
    def test_owner_and_modes_are_checked(self):
        self.assertIn("stat -c '%U %a'", WORKFLOW)
        for expected in ('"root 755"', '"root 644"'):
            self.assertIn(expected, WORKFLOW)

    def test_polkit_defaults_are_checked(self):
        self.assertIn("pkaction --verbose --action-id tw.local.system-device-monitor.cpu-power",
                      WORKFLOW)
        for line in ("implicit any: *no", "implicit inactive: *no",
                     "implicit active: *auth_admin"):
            self.assertIn(line, WORKFLOW)

    def test_units_are_verified_and_hardened(self):
        self.assertIn("systemd-analyze verify", WORKFLOW)
        self.assertIn("systemd-analyze security --offline=true --threshold=10", WORKFLOW)

    def test_helpers_reject_bad_arguments_as_root(self):
        for command in (f'expect 2 sudo "$helper" bogus', 'expect 2 sudo "$service" --protocol',
                        'expect 4 sudo "$helper" status', 'expect 4 sudo "$service"',
                        'expect 4 "$service"'):
            self.assertIn(command, WORKFLOW)
        self.assertIn(f"helper={HELPER}", WORKFLOW)

    def test_service_runs_as_a_dynamic_user_without_capabilities(self):
        for words in ("PKEXEC_UID", "enable", "disable", "CapInh", "CapPrm", "CapEff", "CapBnd",
                      "CapAmb", "NoNewPrivs", "61184 <= uid <= 65519", '"sdm-cpu-power"'):
            self.assertIn(words, WORKFLOW)
        self.assertNotIn('"nobody"', WORKFLOW)

    def test_remove_and_purge_leave_nothing_behind(self):
        self.assertIn("sudo apt-get remove -y system-device-monitor", WORKFLOW)
        self.assertIn("sudo apt-get purge -y system-device-monitor", WORKFLOW)
        for path in ("/etc/systemd/system/system-device-monitor-cpu-power.socket.d",
                     "/etc/systemd/system/sockets.target.wants/system-device-monitor-cpu-power.socket",
                     "/usr/lib/systemd/system/system-device-monitor-cpu-power.service",
                     "/var/lib/system-device-monitor",
                     "/usr/share/polkit-1/actions/tw.local.system-device-monitor.policy"):
            self.assertIn(f"test ! -e {path}", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
