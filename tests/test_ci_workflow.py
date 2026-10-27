"""CI 安裝 .deb 後必須實際檢查管理員元件；避免之後的修改不小心拿掉這些檢查。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT/".github/workflows/ci.yml").read_text(encoding="utf-8")
HELPER = "/usr/libexec/system-device-monitor/sdm-cpu-power"
LIMIT = "/usr/libexec/system-device-monitor/sdm-charge-limit"


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

    def test_update_helper_is_checked_with_rejected_inputs(self):
        self.assertIn("pkaction --verbose --action-id tw.local.system-device-monitor.update",
                      WORKFLOW)
        self.assertIn("tw.local.system-device-monitor.update.policy)\" = \"root 644\"", WORKFLOW)
        for command in ('expect 0 "$update" --protocol', 'expect 2 sudo "$update" install',
                        'fails "$unsigned"', 'fails input "$work/link/$name"',
                        'fails input "$work/big/$name"', 'fails input "$work/root/$name"',
                        'expect 4 sudo "$update" install', "truncate -s 31M",
                        'fails channel "$work/beta/$beta"',
                        'fails "$unsigned" --allow-beta "$work/beta/$beta"'):
            self.assertIn(command, WORKFLOW)
        self.assertNotIn("apt-get install -y \"$work", WORKFLOW)
        self.assertIn("grep -vx .lock", WORKFLOW)       # 暫存目錄只能留下執行鎖

    def test_remove_and_purge_leave_nothing_behind(self):
        self.assertIn("sudo apt-get remove -y system-device-monitor", WORKFLOW)
        self.assertIn("sudo apt-get purge -y system-device-monitor", WORKFLOW)
        for path in ("/etc/systemd/system/system-device-monitor-cpu-power.socket.d",
                     "/etc/systemd/system/sockets.target.wants/system-device-monitor-cpu-power.socket",
                     "/usr/lib/systemd/system/system-device-monitor-cpu-power.service",
                     "/var/lib/system-device-monitor", "/var/cache/system-device-monitor",
                     "/usr/share/polkit-1/actions/tw.local.system-device-monitor.policy"):
            self.assertIn(f"test ! -e {path}", WORKFLOW)



class ChargeLimit(unittest.TestCase):
    """充電上限的小程式、polkit 動作與重新套用的單元：沒有電池的執行器上也要檢查得到的部分。"""

    def test_modes_policy_and_unit(self):
        for text in (f"limit={LIMIT}",
                     "pkaction --verbose --action-id tw.local.system-device-monitor.unattended."
                     "charge-limit", "implicit active: *auth_admin_keep$",
                     'systemd-analyze verify "$limit_unit"',
                     'systemd-analyze security --offline=true --threshold=10 "$limit_unit"',
                     "systemctl is-enabled system-device-monitor-charge-limit.service"):
            self.assertIn(text, WORKFLOW)

    def test_bad_arguments_and_permissions(self):
        for text in ('expect 0 "$limit" --protocol', "expect 2 as_user $args",
                     '"set BAT0 49"', '"set BAT0 101"', '"set BAT0 -80"', '"set BAT0 79.5"',
                     '"set BAT0 8e1"', '"set BAT0 080"', '"set BAT0 1e2"',
                     'expect 2 as_user set BAT0 " 80"', 'expect 4 "$limit" set BAT0 80',
                     'expect 4 sudo "$limit" restore --from-unit'):
            self.assertIn(text, WORKFLOW)

    def test_no_battery_gives_one_json_line_and_exit_two_or_three(self):
        self.assertIn("out=$(as_user set BAT0 80)", WORKFLOW)
        self.assertIn('case "$got" in 2|3)', WORKFLOW)
        self.assertIn("len(lines)==1", WORKFLOW)

    def test_restore_runs_under_real_systemd(self):
        self.assertIn("sudo systemctl start system-device-monitor-charge-limit.service", WORKFLOW)
        self.assertIn("-p Result --value system-device-monitor-charge-limit.service", WORKFLOW)

    def test_package_removal_deletes_the_saved_limit(self):
        remove = WORKFLOW.index("sudo apt-get remove -y system-device-monitor")
        purge = WORKFLOW.index("sudo apt-get purge -y system-device-monitor")
        check = WORKFLOW.index("test ! -e /var/lib/system-device-monitor/charge-limit.json")
        self.assertLess(remove, check)
        self.assertLess(check, purge)
        self.assertNotIn("sudo rm -f /var/lib/system-device-monitor/charge-limit.json", WORKFLOW)
        self.assertIn("test ! -e /run/system-device-monitor/charge-limit.lock", WORKFLOW)
        self.assertIn("test ! -e /run/system-device-monitor/charge-limit.times", WORKFLOW)

    def test_remove_and_purge_leave_nothing_behind(self):
        for path in ("/usr/lib/systemd/system/system-device-monitor-charge-limit.service",
                     "/etc/systemd/system/multi-user.target.wants/"
                     "system-device-monitor-charge-limit.service",
                     "/etc/systemd/system/sleep.target.wants/"
                     "system-device-monitor-charge-limit.service",
                     "/usr/share/polkit-1/actions/"
                     "tw.local.system-device-monitor.unattended.charge-limit.policy"):
            self.assertIn(f"test ! -e {path}", WORKFLOW)

if __name__ == "__main__":
    unittest.main()
