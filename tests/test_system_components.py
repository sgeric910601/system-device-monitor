"""原始碼資料夾安裝管理員元件的使用者端（install.py --system-components）。

git 以假的函式代替（回傳測試自己做的 tar），pkexec 以記錄參數的假函式代替：
測試在假 pkexec 被呼叫的當下檢查暫存目錄的內容、權限與雜湊，結束後確認暫存目錄已刪除。
"""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

from sdm.services import system_components as components
from tests.fixtures.helpers import ROOT, load_script

installer = load_script("install_system")
CHECKOUT = Path("/opt/checkout with space")


def tar_bytes(entries):
    """（名稱、內容或 None 代表目錄、類型）→ tar 位元組。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/shadow"
            if data is not None:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def real_entries():
    directories = sorted({str(Path(name).parent) for name in components.STAGED})
    return ([(name, None, tarfile.DIRTYPE) for name in directories]
            + [(name, (ROOT/name).read_bytes(), tarfile.REGTYPE) for name in components.STAGED])


GIT_DIR = str(CHECKOUT/".git")
HEAD = b"abc1234 feat: add the \x1b[31mCPU\x1b[0m sampler\n"


class FakeGit:
    def __init__(self, status=b"", archive=None, tracked=True, common=b".git", git_dir=GIT_DIR):
        self.status, self.tracked, self.calls, self.common = status, tracked, [], common
        self.git_dir = git_dir
        self.archive = tar_bytes(real_entries()) if archive is None else archive

    def __call__(self, argv):
        self.calls.append(argv)
        command = argv[3]
        if command == "ls-files" and not self.tracked:
            raise components.ComponentError("not tracked")
        if command == "rev-parse":
            return (self.git_dir.encode() if argv[4] == "--absolute-git-dir" else self.common) \
                + b"\n"
        return {"ls-files": b"", "status": self.status, "archive": self.archive,
                "log": HEAD}[command]


def entry(kind="dir", uid=None, mode=0o755, gid=None):
    uid = os.getuid() if uid is None else uid
    bits = {"dir": stat.S_IFDIR, "file": stat.S_IFREG}[kind]
    return SimpleNamespace(st_mode=bits | mode, st_uid=uid, st_gid=os.getgid() if gid is None
                           else gid)


class FakeTree:
    """原始碼資料夾（/opt/checkout with space）用假的 stat，其他路徑（暫存目錄）用真的。"""

    def __init__(self):
        self.entries = {"/": entry(uid=0), "/opt": entry(uid=0), str(CHECKOUT): entry(),
                        GIT_DIR: entry()}

    def __call__(self, path):
        path = str(path)
        if path in self.entries:
            return self.entries[path]
        if path.startswith(str(CHECKOUT)):
            raise FileNotFoundError(path)
        return os.lstat(path)


class Run(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.stages, self.seen, self.messages = [], [], []
        self.exit_status = 0

    def make_stage(self):
        stage = Path(tempfile.mkdtemp(prefix="sdm-system-", dir=self.temp))
        self.stages.append(stage)
        return str(stage)

    def call(self, argv):
        stage = Path(argv[argv.index("--stage") + 1])
        manifest = (stage/"manifest.json").read_bytes()
        self.seen.append(dict(argv=argv, mode=stat.S_IMODE(stage.stat().st_mode),
                              manifest=manifest,
                              files={name: (stage/name).read_bytes()
                                     for name in components.STAGED}))
        return self.exit_status

    def run_action(self, action="install", git=None, deb=False, tree=None, private_gid=None):
        self.git = git or FakeGit()
        self.tree = tree or FakeTree()
        return components.run(action, CHECKOUT, git=self.git, call=self.call,
                              make_stage=self.make_stage, deb_installed=lambda: deb,
                              say=self.messages.append, lstat=self.tree, uid=os.getuid(),
                              private_gid=private_gid)

    def test_stages_a_private_copy_and_runs_pkexec(self):
        self.assertEqual(self.run_action(), 0)
        seen = self.seen[0]
        stage = self.stages[0]
        digest = hashlib.sha256(seen["manifest"]).hexdigest()
        script = stage/"scripts/install_system.py"
        script_digest = hashlib.sha256((ROOT/"scripts/install_system.py").read_bytes()).hexdigest()
        self.assertEqual(seen["argv"], [
            "/usr/bin/pkexec", "/usr/bin/python3", "-I", "-B", "-c", components.BOOTSTRAP,
            str(script), script_digest, "install", "--stage", str(stage),
            "--manifest-sha256", digest])
        self.assertEqual(seen["mode"], 0o700)
        manifest = json.loads(seen["manifest"])
        self.assertEqual(manifest["format"], 1)
        self.assertEqual(sorted(manifest["files"]), sorted(components.SOURCES))
        for name in components.STAGED:
            self.assertEqual(seen["files"][name], (ROOT/name).read_bytes())
        for name in components.SOURCES:
            self.assertEqual(manifest["files"][name],
                             hashlib.sha256((ROOT/name).read_bytes()).hexdigest())
        self.assertFalse(stage.exists())

    def test_manifest_is_accepted_by_the_root_side(self):
        self.run_action()
        files = installer.parse_manifest(self.seen[0]["manifest"])
        self.assertEqual(sorted(files), sorted(components.SOURCES))

    def test_git_reads_only_the_component_paths_from_head(self):
        self.run_action()
        prefix = ["/usr/bin/git", "-C", str(CHECKOUT)]
        self.assertEqual(self.git.calls, [
            prefix + ["rev-parse", "--absolute-git-dir"],
            prefix + ["rev-parse", "--git-common-dir"],
            prefix + ["ls-files", "--error-unmatch", "--", *components.STAGED],
            prefix + ["status", "--porcelain", "--untracked-files=no", "--",
                      *components.STAGED],
            prefix + ["archive", "--format=tar", "HEAD", "--", *components.STAGED],
            prefix + ["log", "-1", "--format=%h %s", "HEAD"]])

    def test_head_commit_is_shown_before_authorization(self):
        order = []
        self.messages = order
        original = self.call
        self.call = lambda argv: order.append("pkexec") or original(argv)
        self.run_action()
        self.assertEqual(order[0], "將以管理員權限安裝這個提交的元件：abc1234 feat: add the "
                                   "[31mCPU[0m sampler")
        self.assertEqual(order[1], "pkexec")

    def test_staged_script_passes_the_bootstrap(self):
        scope = {"__name__": "bootstrap"}
        exec(compile(components.BOOTSTRAP, "<bootstrap>", "exec"), scope)
        loaded = []

        def call(argv):
            loaded.append(scope["load"](argv[5:], {"PKEXEC_UID": str(os.getuid())},
                                        geteuid=lambda: 0))
            return 0
        self.call = call
        self.assertEqual(self.run_action(), 0)
        self.assertEqual(loaded[0][1], (ROOT/"scripts/install_system.py").read_bytes())

    def test_unsafe_repository_is_refused_before_anything_runs(self):
        cases = (
            {str(CHECKOUT): entry(mode=0o775, gid=4242)},
            {str(CHECKOUT): entry(mode=0o757)},
            {str(CHECKOUT): entry(uid=os.getuid() + 1)},
            {"/opt": entry(uid=0, mode=0o777)},
            {GIT_DIR: entry(mode=0o777)},
            {GIT_DIR: entry(uid=os.getuid() + 1)},
        )
        for change in cases:
            with self.subTest(change=sorted(change)):
                tree = FakeTree()
                tree.entries.update(change)
                self.assertEqual(self.run_action(tree=tree, private_gid=1234), 1)
                self.assertIn("原始碼資料夾", self.messages[-1])
        self.assertEqual(self.seen, [])

    def test_user_private_group_may_write_the_checkout(self):
        tree = FakeTree()
        tree.entries[str(CHECKOUT)] = entry(mode=0o775, gid=4242)
        tree.entries[GIT_DIR] = entry(mode=0o775, gid=4242)
        self.assertEqual(self.run_action(tree=tree, private_gid=4242), 0)

    def test_worktree_git_file_and_common_directory_are_checked(self):
        tree = FakeTree()
        tree.entries[GIT_DIR] = entry("file", mode=0o644)
        common, worktree = "/srv/main/.git", "/srv/main/.git/worktrees/x"
        tree.entries.update({"/srv": entry(uid=0), "/srv/main": entry(), common: entry(),
                             common + "/worktrees": entry(), worktree: entry()})
        git = FakeGit(common=common.encode(), git_dir=worktree)
        self.assertEqual(self.run_action(git=git, tree=tree), 0)
        tree.entries["/srv/main"] = entry(mode=0o777)
        git = FakeGit(common=common.encode(), git_dir=worktree)
        self.assertEqual(self.run_action(git=git, tree=tree), 1)

    def test_remove_uses_the_same_staging(self):
        self.assertEqual(self.run_action("remove"), 0)
        self.assertIn("remove", self.seen[0]["argv"])

    def test_modified_files_are_refused(self):
        self.assertEqual(self.run_action(git=FakeGit(status=b" M helper/sdm-cpu-power\n")), 1)
        self.assertEqual(self.seen, [])
        self.assertIn("未提交", self.messages[-1])

    def test_untracked_files_are_refused(self):
        self.assertEqual(self.run_action(git=FakeGit(tracked=False)), 1)
        self.assertEqual(self.seen, [])

    def test_unexpected_archive_members_are_refused(self):
        entries = real_entries()
        cases = (entries + [("helper/extra", b"x", tarfile.REGTYPE)],
                 [entry for entry in entries if entry[0] != "helper/sdm-cpu-power"],
                 [(name, None, tarfile.SYMTYPE) if name == "helper/sdm-cpu-power"
                  else (name, data, kind) for name, data, kind in entries],
                 entries + [("../escape", b"x", tarfile.REGTYPE)],
                 entries + [(components.STAGED[0], b"again", tarfile.REGTYPE)])
        for archive in cases:
            with self.subTest(names=[name for name, _, _ in archive][-1:]):
                self.assertEqual(self.run_action(git=FakeGit(archive=tar_bytes(archive))), 1)
        self.assertEqual(self.seen, [])
        self.assertTrue(all(not stage.exists() for stage in self.stages))

    def test_installed_package_blocks_install_but_not_remove(self):
        self.assertEqual(self.run_action(deb=True), 1)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.run_action("remove", deb=True), 0)

    def test_authorization_failures_are_explained(self):
        for status, words in ((126, "未授權"), (127, "未授權"), (3, "失敗"), (4, "權限")):
            with self.subTest(status=status):
                self.exit_status = status
                self.assertEqual(self.run_action(), status)
                self.assertIn(words, self.messages[-1])

    def test_stage_is_removed_when_pkexec_fails_to_start(self):
        def broken(argv):
            raise OSError("missing pkexec")
        result = components.run("install", CHECKOUT, git=FakeGit(), call=broken,
                                make_stage=self.make_stage, deb_installed=lambda: False,
                                say=self.messages.append, lstat=FakeTree(), private_gid=None)
        self.assertEqual(result, 1)
        self.assertFalse(self.stages[0].exists())

    def test_staged_files_are_not_writable_by_others(self):
        observed = []

        def call(argv):
            stage = Path(argv[argv.index("--stage") + 1])
            observed.extend(stat.S_IMODE(path.lstat().st_mode) for path in stage.rglob("*"))
            return 0

        components.run("install", CHECKOUT, git=FakeGit(), call=call, make_stage=self.make_stage,
                       deb_installed=lambda: False, say=self.messages.append, lstat=FakeTree(),
                       private_gid=None)
        self.assertTrue(observed)
        self.assertTrue(all(mode & 0o077 == 0 for mode in observed))


class StageLocation(unittest.TestCase):
    """暫存目錄放在 XDG_RUNTIME_DIR（屬於使用者、0700），否則只接受 root 擁有且有黏著位元的 /tmp。"""

    def lstat(self, entries):
        def fake(path):
            try:
                return entries[str(path)]
            except KeyError:
                raise FileNotFoundError(path) from None
        return fake

    def tree(self, runtime=None, tmp=None):
        entries = {"/": entry(uid=0), "/run": entry(uid=0), "/run/user": entry(uid=0),
                   "/run/user/1000": runtime or entry(uid=1000, mode=0o700),
                   "/tmp": tmp or entry(uid=0, mode=0o1777)}
        return self.lstat(entries)

    def test_runtime_directory_is_preferred(self):
        self.assertEqual(components.stage_parent({"XDG_RUNTIME_DIR": "/run/user/1000"}, 1000,
                                                 self.tree()), "/run/user/1000")

    def test_unsafe_runtime_directories_fall_back_to_tmp(self):
        for env, runtime in (({}, None), ({"XDG_RUNTIME_DIR": "relative"}, None),
                             ({"XDG_RUNTIME_DIR": "/run/user/1000/"}, None),
                             ({"XDG_RUNTIME_DIR": "/run/user/1000"}, entry(uid=1000, mode=0o750)),
                             ({"XDG_RUNTIME_DIR": "/run/user/1000"}, entry(uid=1001, mode=0o700))):
            with self.subTest(env=env, runtime=runtime):
                self.assertEqual(components.stage_parent(env, 1000, self.tree(runtime)), "/tmp")

    def test_tmp_must_be_root_owned_and_sticky(self):
        for tmp in (entry(uid=0, mode=0o777), entry(uid=1000, mode=0o1777)):
            with self.subTest(tmp=tmp):
                with self.assertRaises(components.ComponentError):
                    components.stage_parent({}, 1000, self.tree(tmp=tmp))

    def test_stage_and_parents_are_checked_before_pkexec(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)/"sdm-system-x"
            stage.mkdir(mode=0o700)
            components.check_stage(str(stage), os.getuid(), os.lstat)
            stage.chmod(0o750)
            with self.assertRaises(components.ComponentError):
                components.check_stage(str(stage), os.getuid(), os.lstat)
            stage.chmod(0o700)
            real = os.lstat

            def unsafe(path):
                info = real(path)
                if str(path) == temp:
                    return SimpleNamespace(st_mode=info.st_mode | 0o002, st_uid=info.st_uid,
                                           st_gid=info.st_gid)
                return info
            with self.assertRaises(components.ComponentError):
                components.check_stage(str(stage), os.getuid(), unsafe)


class Commands(unittest.TestCase):
    def test_install_command_is_quoted_for_the_shell(self):
        self.assertEqual(components.install_command(CHECKOUT),
                         "/usr/bin/python3 -B '/opt/checkout with space/install.py' "
                         "--system-components install")
        self.assertEqual(components.remove_command(Path("/opt/plain")),
                         "/usr/bin/python3 -B /opt/plain/install.py --system-components remove")

    def test_sources_match_the_root_side(self):
        self.assertEqual(components.SOURCES, tuple(source for source, _, _ in installer.FILES))
        self.assertEqual(components.STAGED, components.SOURCES + ("scripts/install_system.py",))

    def test_git_runner_reports_failures(self):
        with self.assertRaises(components.ComponentError):
            components.git_output(["/usr/bin/git", "-C", "/nonexistent-dir", "status"])


class InstallScript(unittest.TestCase):
    def test_install_py_offers_the_option(self):
        text = (ROOT/"install.py").read_text(encoding="utf-8")
        self.assertIn('"--system-components"', text)
        self.assertIn("system_components.run(", text)


if __name__ == "__main__":
    unittest.main()
