"""充電上限的 systemd 單元與 polkit 動作：強化設定、只在使用者選擇保留時啟用、兩種安裝方式一致。"""
import unittest
import xml.etree.ElementTree as ElementTree

from sdm.constants import ChargeLimit
from tests.fixtures.helpers import HELPERS, load_helper
from tests.test_system_units import parse_unit, single

helper = load_helper("sdm-charge-limit")
UNITS = HELPERS/"systemd"
POLICIES = HELPERS/"policy"
HARDENING = {
    "CapabilityBoundingSet": "", "AmbientCapabilities": "", "NoNewPrivileges": "yes",
    "PrivateNetwork": "yes", "IPAddressDeny": "any", "PrivateDevices": "yes", "PrivateTmp": "yes",
    "PrivateIPC": "yes", "ProtectSystem": "strict", "ReadWritePaths": "/sys/devices",
    "ProtectHome": "yes", "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes", "ProtectControlGroups": "yes", "ProtectClock": "yes",
    "ProtectHostname": "yes", "ProtectProc": "invisible", "ProcSubset": "pid",
    "RestrictAddressFamilies": "AF_UNIX", "RestrictNamespaces": "yes",
    "SystemCallArchitectures": "native", "MemoryDenyWriteExecute": "yes", "MemoryMax": "64M",
    "TasksMax": "4", "LockPersonality": "yes", "RestrictRealtime": "yes",
    "RestrictSUIDSGID": "yes", "UMask": "0077",
}
SYSCALLS = ["@system-service", "~@privileged @resources"]
TIMEOUTS = {"TimeoutStartSec": "30s", "TimeoutStopSec": "10s"}
RUNTIME = {"RuntimeDirectory": "system-device-monitor", "RuntimeDirectoryMode": "0755",
           "RuntimeDirectoryPreserve": "yes"}
SERVICE_KEYS = set(HARDENING) | set(TIMEOUTS) | set(RUNTIME) | {"Type", "ExecStart",
                                                                 "SystemCallFilter"}
SLEEP_TARGETS = ("suspend.target", "hibernate.target", "hybrid-sleep.target",
                 "suspend-then-hibernate.target")
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
EXEC_PATH = "org.freedesktop.policykit.exec.path"
DEFAULTS = {"allow_any": "no", "allow_inactive": "no", "allow_active": "auth_admin_keep"}


class RestoreUnits(unittest.TestCase):
    def check_unit(self, name, helper_path, state):
        entries = parse_unit(UNITS/name)
        for key, value in HARDENING.items():
            with self.subTest(unit=name, key=key):
                self.assertEqual(single(entries, "Service", key), value)
        self.assertEqual(entries[("Service", "SystemCallFilter")], SYSCALLS)
        for key, value in {**TIMEOUTS, **RUNTIME}.items():
            with self.subTest(unit=name, key=key):
                self.assertEqual(single(entries, "Service", key), value)
        keys = {key for section, key in entries if section == "Service"}
        self.assertEqual(sorted(keys - SERVICE_KEYS), [])
        self.assertEqual(single(entries, "Service", "Type"), "oneshot")
        self.assertEqual(single(entries, "Service", "ExecStart"),
                         f"{helper_path} restore {helper.FROM_UNIT}")
        self.assertEqual(single(entries, "Unit", "ConditionPathExists"), state)
        self.assertEqual(single(entries, "Unit", "After").split(),
                         ["multi-user.target", *SLEEP_TARGETS])
        self.assertEqual(single(entries, "Install", "WantedBy").split(),
                         ["multi-user.target", "sleep.target"])

    def test_deb_unit(self):
        self.check_unit(ChargeLimit.DEB_UNIT, ChargeLimit.DEB_HELPER, ChargeLimit.DEB_STATE)

    def test_local_unit(self):
        self.check_unit(ChargeLimit.LOCAL_UNIT, ChargeLimit.LOCAL_HELPER, ChargeLimit.LOCAL_STATE)

    def test_runs_as_root_without_capabilities_or_a_user_switch(self):
        entries = parse_unit(UNITS/ChargeLimit.DEB_UNIT)
        for key in ("User", "Group", "DynamicUser", "SupplementaryGroups", "RemainAfterExit",
                    "ExecStartPre", "ExecStartPost", "ExecStop", "Environment",
                    "EnvironmentFile"):
            self.assertNotIn(("Service", key), entries)

    def test_local_unit_only_differs_in_paths(self):
        text = (UNITS/ChargeLimit.DEB_UNIT).read_text(encoding="utf-8")
        for old, new in ((ChargeLimit.DEB_HELPER, ChargeLimit.LOCAL_HELPER),
                         (ChargeLimit.DEB_STATE, ChargeLimit.LOCAL_STATE)):
            text = text.replace(old, new)
        self.assertEqual(text, (UNITS/ChargeLimit.LOCAL_UNIT).read_text(encoding="utf-8"))

    def test_runtime_directory_holds_the_helper_lock(self):
        self.assertEqual("/run/" + RUNTIME["RuntimeDirectory"], ChargeLimit.RUN_DIR)
        self.assertEqual(helper.RUN_DIR, "run/" + RUNTIME["RuntimeDirectory"])

    def test_the_helper_accepts_the_unit_command(self):
        self.assertEqual(helper.parse_args(["restore", helper.FROM_UNIT]), ("restore", None))


def action_of(path):
    root = ElementTree.parse(path).getroot()
    actions = root.findall("action")
    if root.tag != "policyconfig" or len(actions) != 1:
        raise AssertionError("expected one action in " + path.name)
    return actions[0]


class Policies(unittest.TestCase):
    def check(self, action_id, helper_path):
        action = action_of(POLICIES/(action_id + ".policy"))
        self.assertEqual(action.get("id"), action_id)
        self.assertEqual({child.tag: child.text for child in action.find("defaults")}, DEFAULTS)
        # 只有 set 使用這個動作（exec.argv1）；status 改用一般的 policykit.exec（每次都要密碼）
        self.assertEqual({item.get("key"): item.text for item in action.findall("annotate")},
                         {EXEC_PATH: helper_path, "org.freedesktop.policykit.exec.argv1": "set"})
        for tag in ("description", "message"):
            texts = {element.get(LANG): element.text for element in action.findall(tag)}
            self.assertEqual(set(texts), {None, "zh_TW"})

    def test_deb_action(self):
        self.check(ChargeLimit.ACTION_DEB, ChargeLimit.DEB_HELPER)

    def test_local_action(self):
        self.check(ChargeLimit.ACTION_LOCAL, ChargeLimit.LOCAL_HELPER)

    def test_actions_use_the_reserved_unattended_prefix(self):
        for action in (ChargeLimit.ACTION_DEB, ChargeLimit.ACTION_LOCAL):
            self.assertTrue(action.startswith("tw.local.system-device-monitor.unattended."))

    def test_policies_differ_only_in_names_and_paths(self):
        text = (POLICIES/(ChargeLimit.ACTION_DEB + ".policy")).read_text(encoding="utf-8")
        for old, new in ((ChargeLimit.ACTION_DEB, ChargeLimit.ACTION_LOCAL),
                         (ChargeLimit.DEB_HELPER, ChargeLimit.LOCAL_HELPER)):
            text = text.replace(old, new)
        local = (POLICIES/(ChargeLimit.ACTION_LOCAL + ".policy")).read_text(encoding="utf-8")
        self.assertEqual(text.replace("（套件）", "（原始碼安裝）"), local)

    def test_security_notes_set_the_conditions_for_a_future_rule(self):
        text = (HELPERS.parent/"docs/SECURITY.md").read_text(encoding="utf-8")
        for words in ("subject.local", "subject.active", "org.freedesktop.policykit.exec.argv1",
                      f'action.id == "{ChargeLimit.ACTION_DEB}"', 'action.lookup("command_line")',
                      "只涵蓋 set"):
            self.assertIn(words, text)

    def test_no_polkit_rules_are_shipped(self):
        # 只有 polkit 動作（.policy），沒有任何規則檔：「允許無人時自動調整」的規則只在使用者
        # 開啟時由 sdm-unattended 產生，不隨套件安裝；動作的清單由 test_polkit_policy 確認
        self.assertEqual({path.suffix for path in POLICIES.iterdir()}, {".policy"})


if __name__ == "__main__":
    unittest.main()
