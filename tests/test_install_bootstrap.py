"""原始碼安裝的啟動碼（pkexec /usr/bin/python3 -I -B -c BOOTSTRAP …）：root 只執行雜湊相符的那份位元組。

啟動碼在 root 下執行，所以測試把 geteuid、lstat、fstat 注入；暫存目錄是真正的私人目錄。
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from sdm.services import system_components as components

SCRIPT = b"import sys\nRESULT.append(list(sys.argv))\n"


def namespace():
    scope = {"__name__": "sdm_install_bootstrap"}
    exec(compile(components.BOOTSTRAP, "<bootstrap>", "exec"), scope)
    return scope


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.stage = Path(temp.name)/"sdm-system-test"
        (self.stage/"scripts").mkdir(parents=True, mode=0o700)
        self.stage.chmod(0o700)
        self.script = self.stage/"scripts/install_system.py"
        self.script.write_bytes(SCRIPT)
        self.script.chmod(0o600)
        self.digest = hashlib.sha256(SCRIPT).hexdigest()
        self.scope = namespace()
        self.env = {"PKEXEC_UID": str(os.getuid())}

    def load(self, argv=None, env=None, euid=0, **inject):
        argv = argv or ["-c", str(self.script), self.digest, "install"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                result = self.scope["load"](argv, self.env if env is None else env,
                                            geteuid=lambda: euid, **inject)
            except SystemExit as stop:
                self.assertEqual(json.loads(output.getvalue()),
                                 {"ok": False, "error": "bootstrap"})
                return stop.code
        return result


class Load(Base):
    def test_returns_exactly_the_hashed_bytes(self):
        self.assertEqual(self.load(), (str(self.script), SCRIPT))

    def test_tampered_script_is_refused(self):
        self.script.write_bytes(SCRIPT + b"import os\n")
        self.assertEqual(self.load(), 3)

    def test_symlinked_script_is_refused(self):
        real = self.stage/"scripts/real.py"
        self.script.rename(real)
        self.script.symlink_to(real)
        self.assertEqual(self.load(), 3)

    def test_wrong_owner_is_refused(self):
        self.assertEqual(self.load(env={"PKEXEC_UID": str(os.getuid() + 1)}), 3)
        real = os.fstat

        def other_owner(fd):
            info = real(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid + 1,
                                   st_size=info.st_size)
        self.assertEqual(self.load(fstat=other_owner), 3)

    def test_writable_script_or_shared_folder_is_refused(self):
        self.script.chmod(0o620)
        self.assertEqual(self.load(), 3)
        self.script.chmod(0o600)
        (self.stage/"scripts").chmod(0o750)
        self.assertEqual(self.load(), 3)

    def test_unsafe_parent_is_refused(self):
        real = os.lstat
        parent = str(self.stage.parent)

        def group_writable(path):
            info = real(path)
            if path == parent:
                return SimpleNamespace(st_mode=info.st_mode | 0o020, st_uid=info.st_uid)
            return info
        self.assertEqual(self.load(lstat=group_writable), 3)

        def foreign(path):
            info = real(path)
            if path == parent:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid + 7)
            return info
        self.assertEqual(self.load(lstat=foreign), 3)

    def test_sticky_root_directory_is_accepted(self):
        real = os.lstat
        parent = str(self.stage.parent)

        def sticky_tmp(path):
            info = real(path)
            if path == parent:
                return SimpleNamespace(st_mode=0o41777, st_uid=0)
            return info
        self.assertEqual(self.load(lstat=sticky_tmp)[1], SCRIPT)

    def test_fifo_does_not_block(self):
        self.script.unlink()
        os.mkfifo(self.script, 0o600)
        results = []
        thread = threading.Thread(target=lambda: results.append(self.load()), daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [3])

    def test_permissions_and_arguments(self):
        self.assertEqual(self.load(euid=1000), 4)
        for value in ("", "0", "0100", "abc", "١٠٠٠", "-1"):
            with self.subTest(value=value):
                self.assertEqual(self.load(env={"PKEXEC_UID": value}), 4)
        for argv in (["-c"], ["-c", "relative/install_system.py", self.digest],
                     ["-c", str(self.script) + "/", self.digest],
                     ["-c", str(self.stage) + "/scripts/../scripts/install_system.py", self.digest],
                     ["-c", str(self.script), self.digest.upper()],
                     ["-c", str(self.script), self.digest[:-1]]):
            with self.subTest(argv=argv):
                self.assertIn(self.load(argv=argv), (2, 4))


class Run(Base):
    def test_main_executes_the_verified_bytes_with_the_remaining_arguments(self):
        result = []
        argv = ["-c", str(self.script), self.digest, "install", "--stage", "x"]
        with mock.patch("os.geteuid", return_value=0), mock.patch("sys.argv", argv), \
                mock.patch.dict(os.environ, self.env), \
                mock.patch("builtins.RESULT", result, create=True):
            exec(compile(components.BOOTSTRAP, "<bootstrap>", "exec"), {"__name__": "__main__"})
        self.assertEqual(result, [[str(self.script), "install", "--stage", "x"]])

    def test_bootstrap_is_short_and_self_contained(self):
        lines = [line for line in components.BOOTSTRAP.splitlines() if line.strip()]
        self.assertLessEqual(len(lines), 45)
        self.assertNotIn("sdm", components.BOOTSTRAP)
        self.assertIn("O_NOFOLLOW", components.BOOTSTRAP)
        self.assertIn("O_NONBLOCK", components.BOOTSTRAP)


if __name__ == "__main__":
    unittest.main()
