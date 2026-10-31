"""CI 安裝 .deb 後必須實際檢查管理員元件；避免之後的修改不小心拿掉這些檢查。

這裡只能做靜態檢查（文字、YAML 結構與 bash -n）；工作流程本身要在 GitHub 上執行才知道結果。
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from sdm.core.update_logic import AppVersion
from sdm.paths import VERSION

try:
    import yaml
except ImportError:         # 沒有 PyYAML 時略過結構檢查，文字檢查照常
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT/".github/workflows/ci.yml").read_text(encoding="utf-8")
PREVIOUS = re.compile(r"^ +previous=v([0-9][^\s]*)$", re.M)
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



class Leftovers(unittest.TestCase):
    """完全移除之後什麼都不能留下：兩次 purge 之後都執行 scripts/ci_leftovers.sh（它的行為由
    tests/test_ci_leftovers.py 以假的根目錄測試）。"""

    def test_the_check_is_a_committed_script(self):
        self.assertNotIn("leftovers.sh\" <<", WORKFLOW)
        self.assertNotIn("$RUNNER_TEMP/leftovers.sh", WORKFLOW)
        self.assertTrue((ROOT/"scripts/ci_leftovers.sh").is_file())

    def test_the_check_runs_after_every_purge(self):
        purges = [match.start() for match in
                  re.finditer(r"sudo apt-get purge -y system-device-monitor", WORKFLOW)]
        checks = [match.start() for match in re.finditer(r"bash scripts/ci_leftovers\.sh ", WORKFLOW)]
        self.assertEqual(len(purges), 2)
        self.assertEqual(len(checks), 2)
        for purge, check in zip(purges, checks):
            self.assertLess(purge, check)
        self.assertIn('bash scripts/ci_leftovers.sh dist/system-device-monitor_"$(cat VERSION)"_all.deb',
                      WORKFLOW)


class Upgrade(unittest.TestCase):
    """從最後一個發布的版本（git 標籤）升級到這次建置的套件。"""

    def step(self):
        start = WORKFLOW.index("- name: Upgrade from the last release")
        return WORKFLOW[start:]

    def test_the_previous_release_is_older_and_documented(self):
        previous = PREVIOUS.search(self.step()).group(1)
        self.assertLess(AppVersion.current(previous), AppVersion.current(VERSION))
        changelog = (ROOT/"CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"\n## {previous}\n", changelog)

    def test_the_old_package_is_rebuilt_from_its_tag_and_upgraded(self):
        step = self.step()
        order = ['git rev-parse --verify --quiet "refs/tags/$previous^{commit}"',
                 'git archive "$previous" | tar -x -C "$old"',
                 '/usr/bin/python3 -B "$old/scripts/build_deb.py"',
                 'sudo apt-get install -y "$old_deb"',
                 '= "${previous#v}"',
                 'sudo apt-get install -y "./$new_deb"',
                 '= "$(cat VERSION)"',
                 'test ! -e "/usr/share/system-device-monitor/$name"',
                 "sudo apt-get purge -y system-device-monitor",
                 'bash scripts/ci_leftovers.sh "$old_deb" "$new_deb"']
        positions = [step.index(text) for text in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("set -euo pipefail", step)
        self.assertIn("System Device Monitor $(cat VERSION)", step)

    def test_the_upgrade_runs_after_the_component_checks(self):
        self.assertLess(WORKFLOW.index("- name: Remove and purge"),
                        WORKFLOW.index("- name: Upgrade from the last release"))

    def test_the_upgrade_enables_nothing(self):
        step = self.step()
        for text in ("systemctl is-enabled system-device-monitor-cpu-power.socket",
                     "systemctl is-enabled system-device-monitor-charge-limit.service",
                     "sudo test ! -e /etc/polkit-1/rules.d/49-system-device-monitor.rules"):
            with self.subTest(text=text):
                self.assertIn(text, step)


class FailureLogs(unittest.TestCase):
    def test_unit_logs_are_printed_when_a_step_fails(self):
        """執行器上沒辦法事後登入查看：失敗時把單元狀態與小程式的系統紀錄印進 CI 紀錄。"""
        step = WORKFLOW.split("- name: Unit logs after a failure", 1)[1]
        self.assertIn("if: failure()", step)
        for text in ("journalctl", "-t sdm-cpu-power-service", "systemctl status"):
            with self.subTest(text=text):
                self.assertIn(text, step)


@unittest.skipUnless(yaml is not None and shutil.which("bash"), "needs PyYAML and bash")
class Syntax(unittest.TestCase):
    def test_every_step_is_valid_yaml_and_bash(self):
        jobs = yaml.safe_load(WORKFLOW)["jobs"]
        steps = [step for job in jobs.values() for step in job["steps"]]
        self.assertGreater(len(steps), 5)
        with tempfile.TemporaryDirectory() as tmp:
            for number, step in enumerate(steps):
                if "run" not in step:
                    continue
                script = Path(tmp)/f"step{number}.sh"
                script.write_text(step["run"], encoding="utf-8")
                with self.subTest(step=step.get("name")):
                    result = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                                            text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


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


class SmartCharging(unittest.TestCase):
    """定時喚醒與無人授權的小程式、polkit 動作與規則：沒有電池（可能也沒有 RTC）的執行器上也要檢查。"""

    def test_modes_and_polkit_defaults(self):
        for text in ("wake=/usr/libexec/system-device-monitor/sdm-wake",
                     "rules=/usr/libexec/system-device-monitor/sdm-unattended",
                     "check_action tw.local.system-device-monitor.unattended.wake auth_admin_keep",
                     "check_action tw.local.system-device-monitor.unattended.wake-clear "
                     "auth_admin_keep",
                     "check_action tw.local.system-device-monitor.rules auth_admin",
                     "check_action tw.local.system-device-monitor.unattended.status no",
                     "exec.argv1 -> set$", "exec.argv1 -> clear$"):
            self.assertIn(text, WORKFLOW)

    def test_bad_arguments_and_permissions(self):
        for text in ("expect 2 as_wake $args", '"set 1e9"', '"set 01800000000"',
                     "expect 2 as_rules $args", '"grant alice"', 'expect 4 sudo "$wake" status',
                     'expect 4 "$rules" grant', 'expect 0 "$wake" --protocol'):
            self.assertIn(text, WORKFLOW)

    def test_rule_is_granted_checked_and_revoked(self):
        for text in ("as_rules grant", "test \"$(sudo stat -c '%U %a' \"$rule\")\" = \"root 644\"",
                     "'subject.active !== true'", "'pattern.test(line)'",
                     'sudo grep -qF "帳號 uid：$(id -u)" "$rule"', "'(5[0-9]|[6-9][0-9]|100)$/]'",
                     '"account_changed":false', "journalctl -u polkit",
                     "pkcheck --action-id tw.local.system-device-monitor.unattended.status",
                     "as_rules revoke", 'sudo test ! -e "$rule"'):
            self.assertIn(text, WORKFLOW)

    def test_downgrade_revokes_the_rule_before_the_alarm_is_left_for_removal(self):
        """真正的 dpkg 版本比較：降級到 0.3.0~ 以前撤銷規則，升級到測試版時保留。"""
        keep = WORKFLOW.index('sudo "$prerm" upgrade 0.3.0~beta.1')
        same = WORKFLOW.index('sudo "$prerm" upgrade "$(cat VERSION)"')
        drop = WORKFLOW.index('sudo "$prerm" upgrade 0.2.0')
        self.assertIn("prerm=/var/lib/dpkg/info/system-device-monitor.prerm", WORKFLOW)
        self.assertLess(keep, WORKFLOW.index('sudo test -e "$rule"', keep))
        # 重新安裝同一版時 dpkg 也以 upgrade 執行舊的 prerm：規則必須留著
        self.assertLess(same, WORKFLOW.index('sudo test -e "$rule"', same))
        self.assertLess(keep, same)
        self.assertLess(same, drop)
        self.assertLess(drop, WORKFLOW.index('sudo test ! -e "$rule"', drop))
        self.assertLess(keep, drop)
        self.assertLess(drop, WORKFLOW.index("sdm-wake-left"))

    def test_removal_clears_the_alarm_and_the_rule(self):
        for text in ("test ! -e /run/system-device-monitor/wake\n",
                     "sudo test ! -e /etc/polkit-1/rules.d/49-system-device-monitor.rules",
                     "sdm-wake-left",
                     "test ! -e /usr/share/polkit-1/actions/"
                     "tw.local.system-device-monitor.unattended.wake.policy",
                     "test ! -e /usr/share/polkit-1/actions/tw.local.system-device-monitor.rules.policy",
                     "sudo ls /etc/polkit-1/rules.d"):
            self.assertIn(text, WORKFLOW)

    def test_clear_right_after_set_is_not_rate_limited(self):
        start = WORKFLOW.index('as_wake set "$epoch"); got=$?')
        clear = WORKFLOW.index("as_wake clear | grep -q '\"cleared\":true'", start)
        self.assertNotIn("sleep", WORKFLOW[start:clear])
        self.assertIn("sleep 11", WORKFLOW[clear:WORKFLOW.index("sdm-wake-left", clear)])


if __name__ == "__main__":
    unittest.main()
