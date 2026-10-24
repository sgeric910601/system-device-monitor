"""原始碼安裝與真正的 git：filter、include 與 export-subst 都不能改變要以 root 安裝的內容。

測試倉庫建在暫存目錄（umask 022），只用 add、write-tree、commit-tree、update-ref 等不會執行
hooks 的指令建立提交；pkexec 以檢查暫存內容的假函式代替。
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sdm.services import system_components as components
from tests.fixtures.helpers import ROOT

GIT = shutil.which("git")
AUTHOR = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
          "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
SUBST = "helper/systemd/system-device-monitor-local-cpu-power.socket"


@unittest.skipUnless(GIT, "needs git")
class RealGit(unittest.TestCase):
    def setUp(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base/"checkout"
        self.marker = self.base/"filter-ran"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for name in components.STAGED:
            target = self.repo/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT/name).read_bytes())
        self.staged, self.messages = [], []

    def git(self, *args):
        return subprocess.run([GIT, "-C", str(self.repo), *args], check=True,
                              capture_output=True, env={**os.environ, **AUTHOR}).stdout

    def commit(self):
        self.git("add", "--", ".")
        tree = self.git("write-tree").decode().strip()
        commit = self.git("commit-tree", "--no-gpg-sign", "-m", "test", tree).decode().strip()
        self.git("update-ref", "refs/heads/main", commit)

    def call(self, argv):
        stage = Path(argv[argv.index("--stage") + 1])
        self.staged.append({name: (stage/name).read_bytes() for name in components.STAGED})
        return 0

    def run_install(self):
        def make_stage():
            return tempfile.mkdtemp(prefix="sdm-system-", dir=self.base)
        return components.run("install", self.repo, say=self.messages.append, call=self.call,
                              make_stage=make_stage, deb_installed=lambda: False,
                              private_gid=None)

    def evil_filter(self):
        return f"touch '{self.marker}'; cat"

    def test_clean_checkout_installs_the_head_blobs(self):
        self.commit()
        self.assertEqual(self.run_install(), 0)
        self.assertEqual(self.staged[0], {name: (ROOT/name).read_bytes()
                                          for name in components.STAGED})

    def test_included_filter_is_refused_and_never_runs(self):
        (self.repo/".gitattributes").write_text("* filter=x\n")
        self.commit()
        include = self.repo/".git/extra.conf"
        include.write_text(f'[filter "x"]\n\tsmudge = {self.evil_filter()}\n'
                           f'\tclean = {self.evil_filter()}\n')
        self.git("config", "--local", "include.path", str(include))
        self.assertEqual(self.run_install(), 1)
        self.assertIn("git 設定", self.messages[-1])
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.staged, [])

    def test_filter_in_the_local_config_is_refused(self):
        (self.repo/".gitattributes").write_text("* filter=x\n")
        self.commit()
        self.git("config", "--local", "filter.x.smudge", self.evil_filter())
        self.assertEqual(self.run_install(), 1)
        self.assertFalse(self.marker.exists())

    def test_attributes_file_and_conditional_include_are_refused(self):
        self.commit()
        for key, value in (("core.attributesFile", "/dev/null"),
                           ("includeIf.gitdir:/.path", "/dev/null")):
            with self.subTest(key=key):
                self.git("config", "--local", key, value)
                self.assertEqual(self.run_install(), 1)
                self.git("config", "--local", "--unset", key)

    def test_export_subst_and_ident_are_not_applied(self):
        raw = (ROOT/SUBST).read_bytes() + b"# $Format:%H$ $Id$\n"
        (self.repo/SUBST).write_bytes(raw)
        (self.repo/".gitattributes").write_text("* export-subst ident\n")
        self.commit()
        self.assertEqual(self.run_install(), 0)
        self.assertEqual(self.staged[0][SUBST], raw)

    def test_repository_with_several_worktrees(self):
        self.commit()
        self.git("worktree", "add", "--no-checkout", "-q", str(self.base/"second"), "HEAD")
        self.assertEqual(self.run_install(), 0)
        self.git("config", "--local", "extensions.worktreeConfig", "true")
        self.git("config", "--worktree", "filter.x.smudge", self.evil_filter())
        self.assertEqual(self.run_install(), 1)
        self.assertFalse(self.marker.exists())

    def test_working_tree_edits_are_refused(self):
        self.commit()
        with (self.repo/"helper/sdm-cpu-power").open("ab") as handle:
            handle.write(b"# edited\n")
        self.assertEqual(self.run_install(), 1)
        self.assertIn("未提交", self.messages[-1])


if __name__ == "__main__":
    unittest.main()
