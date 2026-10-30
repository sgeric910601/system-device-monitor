"""原始碼資料夾安裝管理員元件的使用者端（install.py --system-components）。

git 以假的函式代替（ls-tree 與 cat-file 回傳專案裡真正的檔案），pkexec 以記錄參數的假函式代替：
測試在假 pkexec 被呼叫的當下檢查暫存目錄的內容、權限與雜湊，結束後確認暫存目錄已刪除。
真正的 git（filter、include、export-subst）見 test_source_install_git。
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from sdm.services import system_components as components
from tests.fixtures.helpers import ROOT, load_script

installer = load_script("install_system")
CHECKOUT = Path("/opt/checkout with space")
GIT_DIR = str(CHECKOUT/".git")
HEAD = b"abc1234 feat: add the \x1b[31mCPU\x1b[0m sampler\n"
SAFE_CONFIG = b"core.repositoryformatversion\n0\0core.bare\nfalse\0remote.origin.url\nx\0"


def blob_id(data, algorithm="sha1"):
    return hashlib.new(algorithm, b"blob %d\0" % len(data) + data).hexdigest()


def tree_listing(names=None, algorithm="sha1", modes=None):
    names = components.STAGED if names is None else names
    modes = modes or {}
    rows = []
    for name in names:
        data = (ROOT/name).read_bytes() if (ROOT/name).is_file() else b""
        kind = "commit" if modes.get(name) == "160000" else "blob"
        rows.append(f"{modes.get(name, '100644')} {kind} {blob_id(data, algorithm)}\t{name}")
    return "\0".join(rows).encode() + b"\0"


class FakeGit:
    def __init__(self, common=b".git", git_dir=GIT_DIR, config=SAFE_CONFIG, listing=None,
                 algorithm="sha1", blobs=None, worktree_config=None):
        self.calls, self.common, self.git_dir, self.config = [], common, git_dir, config
        self.worktree_config = worktree_config
        self.algorithm = algorithm
        self.listing = tree_listing(algorithm=algorithm) if listing is None else listing
        self.blobs = blobs or {blob_id((ROOT/name).read_bytes(), algorithm): (ROOT/name).read_bytes()
                               for name in components.STAGED}

    def __call__(self, argv):
        self.calls.append(argv)
        position = argv.index("-C") + 2
        command, wanted = argv[position], argv[position + 1] if len(argv) > position + 1 else ""
        if command == "rev-parse":
            return {"--absolute-git-dir": self.git_dir.encode(), "--git-common-dir": self.common,
                    "--show-object-format": self.algorithm.encode()}[wanted] + b"\n"
        if command == "config":
            if wanted == "--worktree":
                if self.worktree_config is None:
                    raise components.ComponentError("multiple working trees")
                return self.worktree_config
            return self.config
        if command == "cat-file":
            return self.blobs[argv[-1]]
        return {"ls-tree": self.listing, "log": HEAD}[command]


def entry(kind="dir", uid=None, mode=0o755, gid=None):
    uid = os.getuid() if uid is None else uid
    bits = {"dir": stat.S_IFDIR, "file": stat.S_IFREG, "link": stat.S_IFLNK}[kind]
    return SimpleNamespace(st_mode=bits | mode, st_uid=uid, st_gid=os.getgid() if gid is None
                           else gid)


class FakeTree:
    """原始碼資料夾（/opt/checkout with space）用假的 stat，工作樹內容取自專案裡真正的檔案；
    其他路徑（暫存目錄）用真的。"""

    def __init__(self):
        self.entries = {"/": entry(uid=0), "/opt": entry(uid=0), str(CHECKOUT): entry(),
                        GIT_DIR: entry()}
        for name in components.STAGED:
            parts = name.split("/")
            for end in range(1, len(parts)):
                self.entries.setdefault(str(CHECKOUT/"/".join(parts[:end])), entry())
            self.entries[str(CHECKOUT/name)] = entry("file", mode=0o644)
        self.content = {str(CHECKOUT/name): (ROOT/name).read_bytes() for name in components.STAGED}

    def __call__(self, path):
        path = str(path)
        if path in self.entries:
            return self.entries[path]
        if path.startswith(str(CHECKOUT)):
            raise FileNotFoundError(path)
        return os.lstat(path)

    def listdir(self, path):
        prefix = str(path) + "/"
        return sorted({key[len(prefix):].split("/")[0] for key in self.entries
                       if key.startswith(prefix)})

    def read(self, path):
        return self.content.get(str(path), b"")

    @staticmethod
    def listxattr(path):
        return []


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
                              private_gid=private_gid, listdir=self.tree.listdir,
                              read=self.tree.read, listxattr=self.tree.listxattr,
                              fstype=lambda path: "ext4")

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

    def test_git_reads_only_raw_blobs_from_head(self):
        self.run_action()
        prefix = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                  "-c", "protocol.allow=never", "-c", "core.attributesFile=/dev/null",
                  "-C", str(CHECKOUT)]
        listing = {name: line.split(b" ")[2].split(b"\t")[0].decode()
                   for line in self.git.listing.split(b"\0") if line
                   for name in [line.split(b"\t")[1].decode()]}
        self.assertEqual(self.git.calls, [
            prefix + ["rev-parse", "--absolute-git-dir"],
            prefix + ["rev-parse", "--git-common-dir"],
            prefix + ["config", "--local", "--list", "-z"],
            prefix + ["rev-parse", "--show-object-format"],
            prefix + ["ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *components.STAGED],
            *[prefix + ["cat-file", "blob", listing[name]] for name in components.STAGED],
            prefix + ["log", "-1", "--format=%h %s", "HEAD"]])
        for argv in self.git.calls:
            self.assertNotIn("archive", argv)
            self.assertNotIn("status", argv)

    def test_forbidden_local_config_is_refused_before_reading_anything(self):
        for key in (b"include.path", b"includeif.gitdir:/srv/.path", b"filter.x.smudge",
                    b"filter.lfs.process", b"core.attributesfile", b"INCLUDE.path"):
            with self.subTest(key=key):
                git = FakeGit(config=SAFE_CONFIG + key + b"\nvalue\0")
                self.assertEqual(self.run_action(git=git), 1)
                self.assertIn("git 設定", self.messages[-1])
                commands = [argv[argv.index("-C") + 2] for argv in git.calls]
                self.assertNotIn("ls-tree", commands)
                self.assertNotIn("cat-file", commands)
        self.assertEqual(self.seen, [])

    def test_worktree_config_is_checked_only_when_enabled(self):
        enabled = SAFE_CONFIG + b"extensions.worktreeconfig\ntrue\0"
        git = FakeGit(config=enabled, worktree_config=SAFE_CONFIG)
        self.assertEqual(self.run_action(git=git), 0)
        self.assertIn("--worktree", [argv[argv.index("-C") + 3] for argv in git.calls
                                     if argv[argv.index("-C") + 2] == "config"])
        git = FakeGit(config=enabled, worktree_config=b"filter.x.clean\ncat\0")
        self.assertEqual(self.run_action(git=git), 1)
        self.assertIn("git 設定", self.messages[-1])
        git = FakeGit(config=enabled + b"extensions.worktreeconfig\nfalse\0")
        self.assertEqual(self.run_action(git=git), 0)
        for value in (b"false", b"no", b"0"):
            git = FakeGit(config=SAFE_CONFIG + b"extensions.worktreeconfig\n" + value + b"\0")
            self.assertEqual(self.run_action(git=git), 0)

    def test_sha256_repositories_are_supported(self):
        self.assertEqual(self.run_action(git=FakeGit(algorithm="sha256")), 0)

    def test_blob_that_does_not_match_its_id_is_refused(self):
        git = FakeGit()
        first = next(iter(git.blobs))
        git.blobs[first] = b"tampered"
        self.assertEqual(self.run_action(git=git), 1)
        self.assertEqual(self.seen, [])

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

    def test_foreign_entries_inside_git_metadata_are_refused(self):
        for name in ("objects/ab/cdef", "config", "objects/info/alternates"):
            with self.subTest(name=name):
                tree = FakeTree()
                tree.entries[GIT_DIR + "/objects"] = entry()
                tree.entries[GIT_DIR + "/objects/ab"] = entry()
                tree.entries[GIT_DIR + "/objects/info"] = entry()
                owner = os.getuid() if name.endswith("alternates") else os.getuid() + 9
                tree.entries[GIT_DIR + "/" + name] = entry("file", uid=owner, mode=0o644)
                self.assertEqual(self.run_action(tree=tree), 1)
                self.assertIn("git 目錄", self.messages[-1])
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

    def test_modified_working_tree_is_refused(self):
        name = str(CHECKOUT/"helper/sdm-cpu-power")
        for change in ("content", "symlink", "missing"):
            with self.subTest(change=change):
                tree = FakeTree()
                if change == "content":
                    tree.content[name] += b"# edited\n"
                elif change == "symlink":
                    tree.entries[name] = entry("link", mode=0o777)
                else:
                    del tree.entries[name]
                self.assertEqual(self.run_action(tree=tree), 1)
                self.assertIn("未提交", self.messages[-1])
        self.assertEqual(self.seen, [])

    def test_head_must_hold_exactly_the_expected_regular_files(self):
        staged = list(components.STAGED)
        cases = (tree_listing(staged[1:]),
                 tree_listing(staged + ["helper/extra"]),
                 tree_listing(staged + [staged[0]]),
                 tree_listing(modes={staged[0]: "120000"}),
                 tree_listing(modes={staged[0]: "160000"}),
                 tree_listing(modes={staged[0]: "100664"}),
                 b"garbage\0")
        for listing in cases:
            with self.subTest(listing=listing[:40]):
                self.assertEqual(self.run_action(git=FakeGit(listing=listing)), 1)
        self.assertEqual(self.seen, [])
        self.assertTrue(all(not stage.exists() for stage in self.stages))
        self.assertEqual(self.run_action(git=FakeGit(
            listing=tree_listing(modes={"helper/sdm-cpu-power": "100755"}))), 0)

    def test_installed_package_blocks_install_and_remove(self):
        """兩者擇一：原始碼版的移除會撤銷共用的無人授權規則與喚醒鬧鐘，所以套件在時也不移除。"""
        self.assertEqual(self.run_action(deb=True), 1)
        self.assertEqual(self.messages[-1], components.DEB_INSTALLED)
        self.assertEqual(self.run_action("remove", deb=True), 1)
        self.assertEqual(self.messages[-1], components.DEB_INSTALLED_REMOVE)
        self.assertEqual(self.seen, [])
        text = components.DEB_INSTALLED_REMOVE
        self.assertLess(text.index("sudo apt remove system-device-monitor"),
                        text.index("install.py --system-components remove"))
        self.assertEqual(self.run_action("remove", deb=False), 0)

    def test_authorization_failures_are_explained(self):
        for status, words in ((126, "未授權"), (127, "未授權"), (3, "失敗"), (4, "權限")):
            with self.subTest(status=status):
                self.exit_status = status
                self.assertEqual(self.run_action(), status)
                self.assertIn(words, self.messages[-1])

    def test_stage_is_removed_when_pkexec_fails_to_start(self):
        def broken(argv):
            raise OSError("missing pkexec")
        tree = FakeTree()
        result = components.run("install", CHECKOUT, git=FakeGit(), call=broken,
                                make_stage=self.make_stage, deb_installed=lambda: False,
                                say=self.messages.append, lstat=tree, private_gid=None,
                                listdir=tree.listdir, read=tree.read,
                                listxattr=tree.listxattr)
        self.assertEqual(result, 1)
        self.assertFalse(self.stages[0].exists())

    def test_staged_files_are_not_writable_by_others(self):
        observed = []

        def call(argv):
            stage = Path(argv[argv.index("--stage") + 1])
            observed.extend(stat.S_IMODE(path.lstat().st_mode) for path in stage.rglob("*"))
            return 0

        tree = FakeTree()
        components.run("install", CHECKOUT, git=FakeGit(), call=call, make_stage=self.make_stage,
                       deb_installed=lambda: False, say=self.messages.append, lstat=tree,
                       private_gid=None, listdir=tree.listdir, read=tree.read,
                                listxattr=tree.listxattr)
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

    def test_git_runs_with_a_clean_environment(self):
        seen = {}

        def fake_run(argv, **options):
            seen.update(options, argv=argv)
            return SimpleNamespace(stdout=b"ok")
        environ = {"PATH": "/usr/bin", "HOME": "/var/empty-home", "GIT_DIR": "/elsewhere",
                   "GIT_WORK_TREE": "/x", "GIT_CONFIG_PARAMETERS": "'core.hooksPath'='/tmp'",
                   "GIT_EXEC_PATH": "/evil", "GIT_REPLACE_REF_BASE": "refs/x/"}
        original = components.subprocess.run
        components.subprocess.run = fake_run
        try:
            argv = components.git_command(CHECKOUT, "status")
            self.assertEqual(components.git_output(argv, environ=environ), b"ok")
        finally:
            components.subprocess.run = original
        self.assertEqual(seen["argv"][:10], ["/usr/bin/git", "-c", "core.hooksPath=/dev/null",
                                             "-c", "core.fsmonitor=false", "-c",
                                             "protocol.allow=never", "-c",
                                             "core.attributesFile=/dev/null", "-C"])
        self.assertEqual(seen["env"], {"PATH": "/usr/bin", "HOME": "/var/empty-home",
                                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                       "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ATTR_NOSYSTEM": "1"})

    def test_git_runner_reports_failures(self):
        with self.assertRaises(components.ComponentError):
            components.git_output(["/usr/bin/git", "-C", "/nonexistent-dir", "status"])


class InstallScript(unittest.TestCase):
    def test_install_py_offers_the_option(self):
        text = (ROOT/"install.py").read_text(encoding="utf-8")
        self.assertIn('"--system-components"', text)
        self.assertIn("system_components.run(", text)

    def test_help_names_every_admin_component(self):
        """install.py --help 列出這個指令安裝的每一種元件（CPU 功耗、充電上限、喚醒與無人授權）。"""
        result = subprocess.run([sys.executable, "-B", str(ROOT/"install.py"), "--help"],
                                capture_output=True, text=True, timeout=60, check=True,
                                env={**os.environ, "COLUMNS": "100"})
        text = " ".join(result.stdout.split())
        for words in ("CPU power sampling", "charge limit", "wake alarm", "unattended",
                      "smart charging", "pkexec"):
            with self.subTest(words=words):
                self.assertIn(words, text)
        self.assertIn(" ".join(components.HELP.split()), text)


if __name__ == "__main__":
    unittest.main()
