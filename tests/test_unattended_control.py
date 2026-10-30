"""「允許無人時自動調整」的看板端（sdm.services.unattended_control）：找出小程式、確認通訊協定、
以 pkcheck 查詢規則是否對自己生效、以 pkexec 開關。pkexec 與 pkcheck 以假的執行器代替。"""
from dataclasses import replace
import json
import unittest

from sdm.constants import CpuPower, Unattended
from sdm.services.unattended_control import (DemoUnattended, UnattendedControl, process_spec)
from tests.test_privileged import fake_tree

PKEXEC = CpuPower.PKEXEC
PROTOCOL_OK = '{"protocol":1,"version":"1.0"}\n'
STAT = "4242 (python3 (my app)) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 987654 19 20\n"


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout, done):
        self.calls.append((list(argv), timeout, done))

    def finish(self, status, output="", index=-1):
        self.calls[index][2](status, output)


class Base(unittest.TestCase):
    helpers = (Unattended.DEB_HELPER,)

    def setUp(self):
        self.runner, self.changes = Runner(), []
        self.control = UnattendedControl(self.runner, lambda: self.changes.append(1),
                                         lstat=fake_tree(*self.helpers), pid=lambda: 4242,
                                         uid=lambda: 1000, read=lambda path: STAT)
        self.control.refresh()

    @property
    def state(self):
        return self.control.state

    def protocol_ok(self):
        self.runner.finish(0, PROTOCOL_OK)

    def argv(self, index=-1):
        return self.runner.calls[index][0]


class Discovery(Base):
    def test_process_spec_survives_spaces_and_parentheses_in_the_name(self):
        self.assertEqual(process_spec(4242, 1000, lambda path: STAT), "4242,987654,1000")
        self.assertEqual(process_spec(4242, 1000, lambda path: ""), "4242")

    def test_protocol_then_pkcheck_for_the_check_action(self):
        self.assertEqual(self.argv(), [Unattended.DEB_HELPER, "--protocol"])
        self.assertIsNone(self.state.granted)
        self.protocol_ok()
        self.assertEqual(self.argv(), [Unattended.PKCHECK, "--action-id", Unattended.CHECK_DEB,
                                       "--process", "4242,987654,1000"])
        self.assertEqual(self.runner.calls[-1][1], Unattended.CHECK_TIMEOUT)
        self.runner.finish(0)
        self.assertTrue(self.state.granted)
        self.assertTrue(self.state.ready)

    def test_any_other_pkcheck_result_is_not_granted(self):
        self.protocol_ok()
        for status in (1, 2, 3, 127, None):
            with self.subTest(status=status):
                self.runner.finish(status, "", index=1)
                self.assertFalse(self.state.granted)

    def test_missing_helper_or_wrong_protocol(self):
        self.helpers = ()
        self.setUp()
        self.assertEqual(self.runner.calls, [])
        self.assertIsNone(self.state.layout)
        self.assertFalse(self.state.granted)
        self.helpers = (Unattended.DEB_HELPER,)
        self.setUp()
        self.runner.finish(0, '{"protocol":9}\n')
        self.assertIs(self.state.protocol, False)
        self.assertEqual(len(self.runner.calls), 1)

    def test_local_install(self):
        self.helpers = (Unattended.LOCAL_HELPER,)
        self.setUp()
        self.protocol_ok()
        self.assertIn(Unattended.CHECK_LOCAL, self.argv())


class Switching(Base):
    def setUp(self):
        super().setUp()
        self.protocol_ok()
        self.runner.finish(1)
        self.results = []

    def test_grant_runs_the_helper_through_pkexec_and_checks_again(self):
        self.control.set_granted(True, self.results.append)
        self.assertEqual(self.argv(), [PKEXEC, Unattended.DEB_HELPER, "grant"])
        self.assertEqual(self.runner.calls[-1][1], Unattended.PKEXEC_TIMEOUT)
        self.assertTrue(self.state.busy)
        self.runner.finish(0, json.dumps({"ok": True, "action": "grant", "user": "alice"}))
        self.assertEqual(self.argv()[0], Unattended.PKCHECK)
        self.runner.finish(0)
        self.assertEqual((self.results, self.state.granted, self.state.busy),
                         ([True], True, False))

    def test_revoke(self):
        self.control.set_granted(False, self.results.append)
        self.assertEqual(self.results, [True])          # 已經是關的：不要求授權
        self.control.state = replace(self.control.state, granted=True)
        self.control.set_granted(False, self.results.append)
        self.assertEqual(self.argv(), [PKEXEC, Unattended.DEB_HELPER, "revoke"])
        self.runner.finish(0, json.dumps({"ok": True, "action": "revoke", "removed": True}))
        self.runner.finish(1)
        self.assertEqual((self.results, self.state.granted), ([True, True], False))

    def test_failures_are_explained(self):
        cases = ((126, "", "未授權"), (3, '{"ok":false,"error":"unsupported-user"}', "帳號名稱"),
                 (3, '{"ok":false,"error":"no-rules-directory"}', "rules.d"),
                 (2, '{"ok":false,"error":"usage"}', "重新安裝"), (None, "", "逾時"))
        for status, output, words in cases:
            with self.subTest(status=status, output=output):
                self.control.set_granted(True, self.results.append)
                self.runner.finish(status, output)
                self.assertIn(words, self.state.error)
                self.runner.finish(1)          # 失敗後仍重新確認一次，之後才回報
                self.assertFalse(self.results[-1])
                self.assertFalse(self.state.busy)

    def test_one_switch_at_a_time(self):
        self.control.set_granted(True, self.results.append)
        self.control.set_granted(True, self.results.append)
        self.assertEqual(self.results, [False])
        self.assertEqual(sum(1 for call in self.runner.calls if call[0][0] == PKEXEC), 1)


class Demo(unittest.TestCase):
    def test_demo_never_runs_anything(self):
        changes = []
        demo = DemoUnattended(lambda: changes.append(1), granted=False)
        results = []
        demo.set_granted(True, results.append)
        self.assertEqual((results, demo.state.granted, demo.state.demo), ([True], True, True))
        demo.refresh()
        self.assertTrue(changes)


if __name__ == "__main__":
    unittest.main()
