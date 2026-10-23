"""polkit 動作定義：每種安裝方式一個動作，只允許作用中的使用者以管理員密碼授權。"""
import unittest
import xml.etree.ElementTree as ElementTree

from sdm.constants import CpuPower
from tests.fixtures.helpers import HELPERS

POLICIES = HELPERS/"policy"
DEB_POLICY = POLICIES/"tw.local.system-device-monitor.policy"
LOCAL_POLICY = POLICIES/"tw.local.system-device-monitor.local.policy"
DEFAULTS = {"allow_any": "no", "allow_inactive": "no", "allow_active": "auth_admin"}
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
EXEC_PATH = "org.freedesktop.policykit.exec.path"


def actions(path):
    root = ElementTree.parse(path).getroot()
    if root.tag != "policyconfig":
        raise AssertionError("not a polkit policy")
    return root.findall("action")


class Policies(unittest.TestCase):
    def check(self, path, action_id, helper):
        found = actions(path)
        self.assertEqual([action.get("id") for action in found], [action_id])
        action = found[0]
        defaults = action.find("defaults")
        self.assertEqual({child.tag: child.text for child in defaults}, DEFAULTS)
        annotations = {item.get("key"): item.text for item in action.findall("annotate")}
        self.assertEqual(annotations, {EXEC_PATH: helper})
        for tag in ("description", "message"):
            with self.subTest(tag=tag):
                texts = {element.get(LANG): element.text for element in action.findall(tag)}
                self.assertEqual(set(texts), {None, "zh_TW"})
                self.assertTrue(all(text and text.strip() for text in texts.values()))

    def test_deb_policy(self):
        self.check(DEB_POLICY, CpuPower.ACTION_DEB, CpuPower.DEB_HELPER)

    def test_local_policy(self):
        self.check(LOCAL_POLICY, CpuPower.ACTION_LOCAL, CpuPower.LOCAL_HELPER)

    def test_file_names_match_the_action_ids(self):
        self.assertEqual(sorted(path.name for path in POLICIES.iterdir()),
                         sorted((DEB_POLICY.name, LOCAL_POLICY.name)))
        self.assertTrue(CpuPower.ACTION_DEB.startswith(DEB_POLICY.stem))
        self.assertTrue(CpuPower.ACTION_LOCAL.startswith(LOCAL_POLICY.stem))

    def test_policies_differ_only_in_names_and_paths(self):
        text = DEB_POLICY.read_text(encoding="utf-8")
        for old, new in ((CpuPower.ACTION_DEB, CpuPower.ACTION_LOCAL),
                         (CpuPower.DEB_HELPER, CpuPower.LOCAL_HELPER)):
            text = text.replace(old, new)
        local = LOCAL_POLICY.read_text(encoding="utf-8")
        self.assertEqual(text.replace("（套件）", "（原始碼安裝）"), local)

    def test_helper_paths_are_absolute_and_inside_the_install_locations(self):
        self.assertEqual(CpuPower.DEB_HELPER, CpuPower.DEB_HELPER_DIR + "/sdm-cpu-power")
        self.assertEqual(CpuPower.LOCAL_HELPER, CpuPower.LOCAL_HELPER_DIR + "/sdm-cpu-power")


if __name__ == "__main__":
    unittest.main()
