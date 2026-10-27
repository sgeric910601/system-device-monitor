"""polkit 動作定義：每種安裝方式各一組（更新只有 .deb 才有），只允許本機作用中的使用者以管理員
密碼授權。

- CPU 功耗取樣、「允許無人時自動調整」的開關與安裝更新：每次都要密碼（auth_admin），
  不在 unattended 前綴底下。
- 充電上限的 set、定時喚醒的 set 與 clear：auth_admin_keep，各自以 exec.argv1 綁定子命令，
  只有使用者自己選擇加入的規則能讓它們免密碼；status 沒有自己的動作（改用一般的
  org.freedesktop.policykit.exec，每次都要密碼）。
- unattended.status：沒有任何作用的查詢動作，預設一律拒絕（規則生效時才允許）。
pkexec 依 exec.path 找動作、有 argv1 時再比對第一個參數，第一個符合的就用；所以同一個程式的
動作必須全部都有 argv1 而且各不相同，或只有一個動作。
"""
import unittest
import xml.etree.ElementTree as ElementTree

from sdm.constants import ChargeLimit, CpuPower, Unattended, Update, Wake
from tests.fixtures.helpers import HELPERS

POLICIES = HELPERS/"policy"
DEB_POLICY = POLICIES/"tw.local.system-device-monitor.policy"
LOCAL_POLICY = POLICIES/"tw.local.system-device-monitor.local.policy"
LIMIT_DEB = POLICIES/(ChargeLimit.ACTION_DEB + ".policy")
LIMIT_LOCAL = POLICIES/(ChargeLimit.ACTION_LOCAL + ".policy")
WAKE_DEB = POLICIES/(Wake.ACTION_DEB + ".policy")
WAKE_LOCAL = POLICIES/(Wake.ACTION_LOCAL + ".policy")
RULES_DEB = POLICIES/(Unattended.ACTION_DEB + ".policy")
RULES_LOCAL = POLICIES/(Unattended.ACTION_LOCAL + ".policy")
UPDATE_POLICY = POLICIES/(Update.ACTION + ".policy")
PAIRS = ((DEB_POLICY, LOCAL_POLICY), (LIMIT_DEB, LIMIT_LOCAL), (WAKE_DEB, WAKE_LOCAL),
         (RULES_DEB, RULES_LOCAL))
PASSWORD = {"allow_any": "no", "allow_inactive": "no", "allow_active": "auth_admin"}
KEEP = {"allow_any": "no", "allow_inactive": "no", "allow_active": "auth_admin_keep"}
NEVER = {"allow_any": "no", "allow_inactive": "no", "allow_active": "no"}
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
EXEC_PATH = "org.freedesktop.policykit.exec.path"
ARGV1 = "org.freedesktop.policykit.exec.argv1"
PREFIX = "tw.local.system-device-monitor.unattended."
# 動作 →（預設、註記）
EXPECTED = {
    CpuPower.ACTION_DEB: (PASSWORD, {EXEC_PATH: CpuPower.DEB_HELPER}),
    CpuPower.ACTION_LOCAL: (PASSWORD, {EXEC_PATH: CpuPower.LOCAL_HELPER}),
    ChargeLimit.ACTION_DEB: (KEEP, {EXEC_PATH: ChargeLimit.DEB_HELPER, ARGV1: "set"}),
    ChargeLimit.ACTION_LOCAL: (KEEP, {EXEC_PATH: ChargeLimit.LOCAL_HELPER, ARGV1: "set"}),
    Wake.ACTION_DEB: (KEEP, {EXEC_PATH: Wake.DEB_HELPER, ARGV1: "set"}),
    Wake.ACTION_CLEAR_DEB: (KEEP, {EXEC_PATH: Wake.DEB_HELPER, ARGV1: "clear"}),
    Wake.ACTION_LOCAL: (KEEP, {EXEC_PATH: Wake.LOCAL_HELPER, ARGV1: "set"}),
    Wake.ACTION_CLEAR_LOCAL: (KEEP, {EXEC_PATH: Wake.LOCAL_HELPER, ARGV1: "clear"}),
    Unattended.ACTION_DEB: (PASSWORD, {EXEC_PATH: Unattended.DEB_HELPER}),
    Unattended.ACTION_LOCAL: (PASSWORD, {EXEC_PATH: Unattended.LOCAL_HELPER}),
    Unattended.CHECK_DEB: (NEVER, {}),
    Unattended.CHECK_LOCAL: (NEVER, {}),
    Update.ACTION: (PASSWORD, {EXEC_PATH: Update.HELPER}),
}


def actions(path):
    root = ElementTree.parse(path).getroot()
    if root.tag != "policyconfig":
        raise AssertionError("not a polkit policy")
    return root.findall("action")


def all_actions():
    return [action for path in sorted(POLICIES.iterdir()) for action in actions(path)]


def annotations(action):
    return {item.get("key"): item.text for item in action.findall("annotate")}


class Policies(unittest.TestCase):
    def test_every_action_has_the_expected_defaults_and_annotations(self):
        found = {action.get("id"): action for action in all_actions()}
        self.assertEqual(sorted(found), sorted(EXPECTED))
        for action_id, (defaults, notes) in EXPECTED.items():
            with self.subTest(action=action_id):
                action = found[action_id]
                self.assertEqual({child.tag: child.text for child in action.find("defaults")},
                                 defaults)
                self.assertEqual(annotations(action), notes)

    def test_descriptions_and_messages_in_english_and_chinese(self):
        for action in all_actions():
            for tag in ("description", "message"):
                with self.subTest(action=action.get("id"), tag=tag):
                    texts = {element.get(LANG): element.text for element in action.findall(tag)}
                    self.assertEqual(set(texts), {None, "zh_TW"})
                    self.assertTrue(all(text and text.strip() for text in texts.values()))

    def test_file_names_start_their_first_action(self):
        names = sorted(path.name for path in POLICIES.iterdir())
        self.assertEqual(names, sorted([path.name for pair in PAIRS for path in pair]
                                       + [UPDATE_POLICY.name]))
        for path in POLICIES.iterdir():
            with self.subTest(path=path.name):
                self.assertTrue(actions(path)[0].get("id").startswith(path.stem))

    def test_variants_differ_only_in_names_and_paths(self):
        swaps = ((CpuPower.ACTION_DEB, CpuPower.ACTION_LOCAL),
                 (CpuPower.DEB_HELPER_DIR, CpuPower.LOCAL_HELPER_DIR),
                 (PREFIX, PREFIX + "local."),
                 (Unattended.ACTION_DEB, Unattended.ACTION_LOCAL))
        for deb, local in PAIRS:
            with self.subTest(policy=deb.name):
                text = deb.read_text(encoding="utf-8")
                for old, new in swaps:
                    text = text.replace(old, new)
                self.assertEqual(text.replace("（套件）", "（原始碼安裝）"),
                                 local.read_text(encoding="utf-8"))

    def test_update_is_never_installed_from_a_source_checkout(self):
        from sdm.services import system_components
        for name in system_components.STAGED:
            self.assertNotIn("update", name)

    def test_pkexec_picks_exactly_one_action_per_program_and_argument(self):
        by_path = {}
        for action in all_actions():
            notes = annotations(action)
            if EXEC_PATH in notes:
                by_path.setdefault(notes[EXEC_PATH], []).append(notes.get(ARGV1))
        for path, arguments in by_path.items():
            with self.subTest(path=path):
                if len(arguments) > 1:
                    self.assertNotIn(None, arguments)
                self.assertEqual(len(arguments), len(set(arguments)))

    def test_helper_paths_are_absolute_and_inside_the_install_locations(self):
        self.assertEqual(CpuPower.DEB_HELPER, CpuPower.DEB_HELPER_DIR + "/sdm-cpu-power")
        self.assertEqual(CpuPower.LOCAL_HELPER, CpuPower.LOCAL_HELPER_DIR + "/sdm-cpu-power")
        for deb, local in ((Wake.DEB_HELPER, Wake.LOCAL_HELPER),
                           (Unattended.DEB_HELPER, Unattended.LOCAL_HELPER)):
            self.assertTrue(deb.startswith(CpuPower.DEB_HELPER_DIR + "/"))
            self.assertTrue(local.startswith(CpuPower.LOCAL_HELPER_DIR + "/"))


class ReservedPrefix(unittest.TestCase):
    """unattended 前綴只用在可以由使用者選擇加入的規則免密碼的動作；需要密碼的動作絕不能用。"""

    def test_password_actions_avoid_the_unattended_prefix(self):
        for action in all_actions():
            defaults = {child.tag: child.text for child in action.find("defaults")}
            if defaults["allow_active"] == "auth_admin":
                self.assertFalse(action.get("id").startswith(PREFIX), action.get("id"))

    def test_only_these_actions_use_the_prefix(self):
        prefixed = sorted(action.get("id") for action in all_actions()
                          if action.get("id").startswith(PREFIX))
        self.assertEqual(prefixed, sorted((ChargeLimit.ACTION_DEB, ChargeLimit.ACTION_LOCAL,
                                           Wake.ACTION_DEB, Wake.ACTION_CLEAR_DEB,
                                           Wake.ACTION_LOCAL, Wake.ACTION_CLEAR_LOCAL,
                                           Unattended.CHECK_DEB, Unattended.CHECK_LOCAL)))

    def test_security_notes_reserve_the_prefix_and_require_full_ids(self):
        text = (HELPERS.parent/"docs/SECURITY.md").read_text(encoding="utf-8")
        self.assertIn(PREFIX + "*", text)
        self.assertIn("完整的動作 ID", text)


if __name__ == "__main__":
    unittest.main()
