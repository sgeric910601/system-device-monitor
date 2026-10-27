"""無人授權開關小程式（helper/sdm-unattended）：規則範本、帳號名稱驗證、檔案位置與權限、
撤銷只刪那個檔案，以及規則本身的判斷（以 polkitd 使用的 duktape 實際執行）。

以假根目錄（擁有者＝執行測試的使用者）代替 /；不需要 root，也不會碰到真正的
/etc/polkit-1/rules.d。
"""
import fcntl
import os
import re
import stat
import unittest

from sdm.constants import ChargeLimit, CpuPower, Unattended, Wake
from tests.fixtures import duktape
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import ROOT, git_mode, load_helper

helper = load_helper("sdm-unattended")
UID = 1000
ENV = {"PKEXEC_UID": str(UID)}
RULES = "etc/polkit-1/rules.d"
RULE = RULES + "/" + Unattended.RULE_NAME
LOCK = "run/system-device-monitor/" + Unattended.LOCK_NAME
GRANTED_IDS = (ChargeLimit.ACTION_DEB, ChargeLimit.ACTION_LOCAL, Wake.ACTION_DEB,
               Wake.ACTION_CLEAR_DEB, Wake.ACTION_LOCAL, Wake.ACTION_CLEAR_LOCAL,
               Unattended.CHECK_DEB, Unattended.CHECK_LOCAL)
BAD_NAMES = ("Alice", "a b", 'a"b', "a\\b", "", "-alice", "9alice", "a" * 33, "élan", "a.b",
             "alice\n", "al;ice", "al'ice", "a/b", "alice$")


class Base(unittest.TestCase):
    helper_path = Unattended.DEB_HELPER

    def setUp(self):
        self.root = FakeRoot(self)
        self.output, self.logged, self.sleeps, self.now = [], [], [], 0.0
        self.names, self.uids = {UID: "alice"}, {}
        for path in (Unattended.DEB_HELPER, Unattended.LOCAL_HELPER):
            self.root.file(path.lstrip("/"), mode=0o755)
        self.root.directory("run")
        rules = self.root.directory(RULES)
        os.chmod(rules, 0o750)                 # Ubuntu：root:polkitd 0750

    def user_name(self, uid):
        return self.names[uid]

    def user_uid(self, name):
        if name in self.uids:
            return self.uids[name]
        for uid, known in self.names.items():
            if known == name:
                return uid
        raise KeyError(name)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def context(self, euid=0, helper_path=None, uid_range=(1000, 60000)):
        return helper.Context(
            root=str(self.root.path), owner=os.getuid(), euid=euid,
            user_exists=lambda uid: uid in self.names, user_name=self.user_name,
            user_uid=self.user_uid, helper_path=helper_path or self.helper_path,
            uid_range=uid_range, sleep=self.sleep, clock=lambda: self.now)

    def main(self, argv, env=ENV, **options):
        return helper.main(list(argv), dict(env), self.context(**options),
                           emit=self.output.append, report=self.logged.append)

    def rule(self):
        return (self.root.path/RULE).read_text(encoding="utf-8")


class Arguments(Base):
    def test_bad_commands_exit_two(self):
        for argv in ([], [""], ["bogus"], ["GRANT"], ["grant", "alice"], ["grant", "--user"],
                     ["revoke", "x"], ["status", "x"], ["--protocol", "x"], ["--help"]):
            with self.subTest(argv=argv):
                self.output.clear()
                self.assertEqual(self.main(argv, env={}, euid=1000), helper.EXIT_USAGE)
                self.assertEqual(self.output, [{"ok": False, "error": "usage"}])
        self.assertFalse((self.root.path/RULE).exists())

    def test_protocol_needs_no_privileges(self):
        self.assertEqual(self.main(["--protocol"], env={}, euid=1000), helper.EXIT_OK)
        self.assertEqual(self.output, [{"protocol": Unattended.PROTOCOL,
                                        "version": helper.VERSION}])


class Permissions(Base):
    def test_every_command_needs_root_through_pkexec(self):
        for argv in (["grant"], ["revoke"], ["status"]):
            for options in ({"euid": 1000}, {"env": {}}, {"env": {"PKEXEC_UID": "0"}},
                            {"env": {"PKEXEC_UID": "65534"}}, {"env": {"PKEXEC_UID": "999"}},
                            {"env": {"PKEXEC_UID": "1001"}}, {"env": {"PKEXEC_UID": "01000"}}):
                with self.subTest(argv=argv, options=options):
                    self.output.clear()
                    self.assertEqual(self.main(argv, **options), helper.EXIT_NOT_PERMITTED)
        self.assertFalse((self.root.path/RULE).exists())

    def test_exact_install_paths_only(self):
        for path in ("/usr/bin/sdm-unattended",
                     "//usr/libexec/system-device-monitor/sdm-unattended",
                     "/usr/libexec/system-device-monitor/sdm-wake"):
            with self.subTest(path=path):
                self.output.clear()
                self.assertEqual(self.main(["grant"], helper_path=path), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "unknown-location"}])
        self.assertFalse((self.root.path/RULE).exists())


class Grant(Base):
    def test_writes_the_fixed_template_for_the_calling_user(self):
        previous = os.umask(0o077)
        try:
            self.assertEqual(self.main(["grant"]), helper.EXIT_OK)
        finally:
            os.umask(previous)
        self.assertEqual(self.output, [{"ok": True, "action": "grant", "user": "alice"}])
        self.assertEqual(self.rule(), helper.render_rule("alice"))
        path = self.root.path/RULE
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.root.path/RULES).stat().st_mode), 0o750)
        self.assertEqual(self.logged, ["grant ok"])
        self.assertEqual(sorted(name for name in os.listdir(self.root.path/RULES)),
                         [Unattended.RULE_NAME])

    def test_grant_again_replaces_the_rule_for_the_new_user(self):
        self.main(["grant"])
        self.names[1001] = "bob"
        self.assertEqual(self.main(["grant"], env={"PKEXEC_UID": "1001"}), helper.EXIT_OK)
        self.assertIn('var user = "bob";', self.rule())
        self.assertNotIn("alice", self.rule())

    def test_unsupported_user_names_are_refused(self):
        for name in BAD_NAMES:
            with self.subTest(name=name):
                self.names[UID] = name
                self.output.clear()
                self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "unsupported-user"}])
        self.assertFalse((self.root.path/RULE).exists())

    def test_name_must_map_back_to_the_same_uid(self):
        self.uids["alice"] = 1001          # 名稱對回去是另一個帳號
        self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "unsupported-user"}])

    def test_rules_directory_is_checked_level_by_level(self):
        rules = self.root.path/RULES
        for path, mode in ((rules, 0o770), (rules, 0o757), (rules.parent, 0o775),
                           (self.root.path/"etc", 0o777)):
            with self.subTest(path=path, mode=oct(mode)):
                original = stat.S_IMODE(path.stat().st_mode)
                path.chmod(mode)
                self.output.clear()
                self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "untrusted-directory"}])
                path.chmod(original)
        self.assertFalse((self.root.path/RULE).exists())

    def test_missing_or_symlinked_rules_directory(self):
        rules = self.root.path/RULES
        rules.rmdir()
        self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "no-rules-directory"})
        elsewhere = self.root.directory("run/rules")
        rules.symlink_to(elsewhere)
        self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_existing_entry_that_is_not_a_file_is_refused(self):
        target = self.root.file("run/elsewhere.rules", "keep\n")
        (self.root.path/RULE).symlink_to(target)
        self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual(self.output[-1], {"ok": False, "error": "unexpected-file"})


class Revoke(Base):
    def test_removes_exactly_that_file(self):
        self.main(["grant"])
        other = self.root.file(RULES + "/50-other.rules", "// other\n")
        self.assertEqual(self.main(["revoke"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "revoke", "removed": True})
        self.assertFalse((self.root.path/RULE).exists())
        self.assertTrue(other.exists())
        self.assertEqual(self.main(["revoke"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "revoke", "removed": False})

    def test_directory_with_the_rule_name_is_left_alone(self):
        self.root.directory(RULE)
        self.assertEqual(self.main(["revoke"]), helper.EXIT_FAILED)
        self.assertTrue((self.root.path/RULE).is_dir())

    def test_symlink_with_the_rule_name_is_removed_but_not_followed(self):
        target = self.root.file("run/elsewhere.rules", "keep\n")
        (self.root.path/RULE).symlink_to(target)
        self.assertEqual(self.main(["revoke"]), helper.EXIT_OK)
        self.assertFalse(os.path.lexists(self.root.path/RULE))
        self.assertEqual(target.read_text(), "keep\n")


class Status(Base):
    def test_status_reports_the_user_and_modifications(self):
        self.assertEqual(self.main(["status"]), helper.EXIT_OK)
        self.assertEqual(self.output[-1], {"ok": True, "action": "status", "granted": False,
                                           "user": None, "modified": False})
        self.main(["grant"])
        self.main(["status"])
        self.assertEqual(self.output[-1], {"ok": True, "action": "status", "granted": True,
                                           "user": "alice", "modified": False})
        (self.root.path/RULE).write_text(self.rule().replace("subject.active !== true", "false"))
        self.main(["status"])
        self.assertEqual(self.output[-1], {"ok": True, "action": "status", "granted": False,
                                           "user": None, "modified": True})

    def test_lock_is_private_and_serializes_runs(self):
        self.main(["status"])
        path = self.root.path/LOCK
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertEqual(self.main(["grant"]), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "busy"})
        self.assertFalse((self.root.path/RULE).exists())


class Template(unittest.TestCase):
    def setUp(self):
        self.text = helper.render_rule("alice")

    def test_only_the_user_name_is_substituted(self):
        self.assertEqual(helper.RULE_TEMPLATE.count(helper.USER_MARK), 1)
        self.assertEqual(self.text, helper.RULE_TEMPLATE.replace(helper.USER_MARK, "alice"))
        self.assertIn('var user = "alice";', self.text)
        with self.assertRaises(helper.Failed):
            helper.render_rule('alice"; polkit.addRule(function () { return "yes"; }); //')

    def test_exact_action_ids_and_helper_paths(self):
        ids = re.findall(r'"(tw\.local\.[^"]+)"', self.text)
        self.assertEqual(sorted(ids), sorted(GRANTED_IDS))
        paths = set(re.findall(r'"(/[^"]+)"', self.text))
        self.assertEqual(paths, {ChargeLimit.DEB_HELPER, ChargeLimit.LOCAL_HELPER,
                                 Wake.DEB_HELPER, Wake.LOCAL_HELPER})
        for word in ("indexOf", "startsWith", "match(", "RegExp", "cpu-power", ".rules\"",
                     "AUTH_", "NO;"):
            self.assertNotIn(word, self.text)

    def test_conditions_are_all_required(self):
        for condition in ("subject.user !== user", "subject.local !== true",
                          "subject.active !== true", 'action.lookup("program") !== program',
                          'action.lookup("user") !== "root"'):
            self.assertIn(condition, self.text)
        self.assertEqual(self.text.count("polkit.Result.YES"), 2)

    def test_es5_only(self):
        for word in ("=>", "let ", "const ", "`", "class "):
            self.assertNotIn(word, self.text)


ACTIVE = {"user": "alice", "local": True, "active": True}
LIMIT_SET = {"program": ChargeLimit.DEB_HELPER, "user": "root",
             "command_line": ChargeLimit.DEB_HELPER + " set BAT0 80"}
WAKE_CLEAR = {"program": Wake.DEB_HELPER, "user": "root", "command_line": Wake.DEB_HELPER + " clear"}


@unittest.skipUnless(duktape.AVAILABLE, "needs libduktape (the polkitd JavaScript engine)")
class Decisions(unittest.TestCase):
    """規則在 polkitd 的引擎中的實際判斷：只有完全符合時才是 yes，其他一律不做決定。"""

    def decide(self, *cases):
        return duktape.decide(helper.render_rule("alice"), [list(case) for case in cases])

    def test_allowed_cases(self):
        local_set = {"program": ChargeLimit.LOCAL_HELPER, "user": "root",
                     "command_line": ChargeLimit.LOCAL_HELPER + " set BAT0 80 --persist on"}
        wake_set = {"program": Wake.DEB_HELPER, "user": "root",
                    "command_line": Wake.DEB_HELPER + " set 1800000600"}
        local_clear = {"program": Wake.LOCAL_HELPER, "user": "root",
                       "command_line": Wake.LOCAL_HELPER + " clear"}
        self.assertEqual(self.decide((ChargeLimit.ACTION_DEB, LIMIT_SET, ACTIVE),
                                     (ChargeLimit.ACTION_LOCAL, local_set, ACTIVE),
                                     (Wake.ACTION_DEB, wake_set, ACTIVE),
                                     (Wake.ACTION_CLEAR_DEB, WAKE_CLEAR, ACTIVE),
                                     (Wake.ACTION_CLEAR_LOCAL, local_clear, ACTIVE),
                                     (Unattended.CHECK_DEB, {}, ACTIVE),
                                     (Unattended.CHECK_LOCAL, {}, ACTIVE)), ["yes"] * 7)

    def test_other_users_remote_or_inactive_sessions(self):
        cases = [dict(ACTIVE, user="bob"), dict(ACTIVE, local=False), dict(ACTIVE, active=False),
                 dict(ACTIVE, local="true"), dict(ACTIVE, user="alice ")]
        self.assertEqual(self.decide(*[(ChargeLimit.ACTION_DEB, LIMIT_SET, subject)
                                       for subject in cases]
                                     + [(Unattended.CHECK_DEB, {}, subject) for subject in cases]),
                         ["none"] * 10)

    def test_other_actions(self):
        for action in ("org.freedesktop.login1.reboot", CpuPower.ACTION_DEB,
                       Unattended.ACTION_DEB, ChargeLimit.ACTION_DEB + ".extra",
                       "tw.local.system-device-monitor.unattended.other",
                       "tw.local.system-device-monitor.unattended.", "constructor", "toString"):
            with self.subTest(action=action):
                self.assertEqual(self.decide((action, LIMIT_SET, ACTIVE)), ["none"])

    def test_details_must_match_the_helper_and_command(self):
        cases = (dict(LIMIT_SET, command_line=ChargeLimit.DEB_HELPER + " status"),
                 dict(LIMIT_SET, command_line=ChargeLimit.DEB_HELPER + " set"),
                 dict(LIMIT_SET, command_line=ChargeLimit.DEB_HELPER + " setx BAT0 80"),
                 dict(LIMIT_SET, command_line="/tmp/sdm-charge-limit set BAT0 80"),
                 dict(LIMIT_SET, program=ChargeLimit.LOCAL_HELPER),
                 dict(LIMIT_SET, user="nobody"),
                 {"program": ChargeLimit.DEB_HELPER, "user": "root"},
                 {"command_line": LIMIT_SET["command_line"], "user": "root"})
        self.assertEqual(self.decide(*[(ChargeLimit.ACTION_DEB, details, ACTIVE)
                                       for details in cases]), ["none"] * len(cases))
        clears = (dict(WAKE_CLEAR, command_line=Wake.DEB_HELPER + " clear now"),
                  dict(WAKE_CLEAR, command_line=Wake.DEB_HELPER + " set 1800000600"),
                  dict(WAKE_CLEAR, command_line=Wake.DEB_HELPER + " status"))
        self.assertEqual(self.decide(*[(Wake.ACTION_CLEAR_DEB, details, ACTIVE)
                                       for details in clears]), ["none"] * 3)
        self.assertEqual(self.decide((Wake.ACTION_DEB, WAKE_CLEAR, ACTIVE)), ["none"])

    def test_rule_compiles_in_the_polkit_engine(self):
        duktape.run(duktape.INIT + helper.render_rule("alice") + "\n1;")


class Consistency(unittest.TestCase):
    def test_constants_match_the_app(self):
        self.assertEqual(helper.PROTOCOL, Unattended.PROTOCOL)
        self.assertEqual(tuple(layout.helper_dir + "/" + helper.HELPER_NAME
                               for layout in helper.LAYOUTS),
                         (Unattended.DEB_HELPER, Unattended.LOCAL_HELPER))
        self.assertEqual("/" + helper.RULES_DIR + "/" + helper.RULE_NAME, Unattended.RULE_FILE)
        self.assertEqual(helper.RULE_NAME, Unattended.RULE_NAME)
        self.assertEqual(helper.USER_PATTERN.pattern, Unattended.USER_PATTERN)
        self.assertEqual(helper.LOCK_NAME, Unattended.LOCK_NAME)
        self.assertEqual("/" + helper.RUN_DIR, ChargeLimit.RUN_DIR)
        self.assertEqual((helper.EXIT_OK, helper.EXIT_USAGE, helper.EXIT_FAILED,
                          helper.EXIT_NOT_PERMITTED), CpuPower.EXIT_CODES)

    def test_helper_is_self_contained_and_isolated(self):
        text = (ROOT/"helper/sdm-unattended").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/python3 -IB\n"))
        for forbidden in ("import sdm", "from sdm", "sys.path", "subprocess", "os.system",
                          "shell=True"):
            self.assertNotIn(forbidden, text)
        self.assertIn(git_mode(ROOT/"helper/sdm-unattended"), (None, "100755"))


if __name__ == "__main__":
    unittest.main()
