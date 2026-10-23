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


class FakeGit:
    def __init__(self, status=b"", archive=None, tracked=True):
        self.status, self.tracked, self.calls = status, tracked, []
        self.archive = tar_bytes(real_entries()) if archive is None else archive

    def __call__(self, argv):
        self.calls.append(argv)
        command = argv[3]
        if command == "ls-files" and not self.tracked:
            raise components.ComponentError("not tracked")
        return {"ls-files": b"", "status": self.status, "archive": self.archive}[command]


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

    def run_action(self, action="install", git=None, deb=False):
        self.git = git or FakeGit()
        return components.run(action, CHECKOUT, git=self.git, call=self.call,
                              make_stage=self.make_stage, deb_installed=lambda: deb,
                              say=self.messages.append)

    def test_stages_a_private_copy_and_runs_pkexec(self):
        self.assertEqual(self.run_action(), 0)
        seen = self.seen[0]
        stage = self.stages[0]
        digest = hashlib.sha256(seen["manifest"]).hexdigest()
        self.assertEqual(seen["argv"], [
            "/usr/bin/pkexec", "/usr/bin/python3", "-I", "-B",
            str(stage/"scripts/install_system.py"), "install", "--stage", str(stage),
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
            prefix + ["ls-files", "--error-unmatch", "--", *components.STAGED],
            prefix + ["status", "--porcelain", "--untracked-files=no", "--",
                      *components.STAGED],
            prefix + ["archive", "--format=tar", "HEAD", "--", *components.STAGED]])

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
                                say=self.messages.append)
        self.assertEqual(result, 1)
        self.assertFalse(self.stages[0].exists())

    def test_staged_files_are_not_writable_by_others(self):
        observed = []

        def call(argv):
            stage = Path(argv[argv.index("--stage") + 1])
            observed.extend(stat.S_IMODE(path.lstat().st_mode) for path in stage.rglob("*"))
            return 0

        components.run("install", CHECKOUT, git=FakeGit(), call=call, make_stage=self.make_stage,
                       deb_installed=lambda: False, say=self.messages.append)
        self.assertTrue(observed)
        self.assertTrue(all(mode & 0o077 == 0 for mode in observed))


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
