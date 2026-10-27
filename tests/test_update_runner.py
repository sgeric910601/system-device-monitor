"""一鍵更新的 pkexec 執行器：一直等到程序真的結束才回報，超過時限只通知「仍在安裝中」，
絕不放棄或結束程序。在 GLib 主迴圈中執行暫存目錄裡的假程式（不會執行 pkexec）。"""
from pathlib import Path
import tempfile
import unittest

from gi.repository import GLib

from sdm.services.update_runner import PatientRunner

WAIT_S = 10


class Runner(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def program(self, body):
        path = self.dir/"fake-helper"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return str(path)

    def run_and_wait(self, argv, slow_after):
        """執行並等待回報（最多 WAIT_S 秒）；回傳（done 的結果、on_slow 被呼叫的次數）。"""
        loop, results, slow = GLib.MainLoop(), [], []

        def done(status, output):
            results.append((status, output))
            if loop.is_running():
                loop.quit()

        def give_up():
            loop.quit()
            return False
        PatientRunner().run(argv, slow_after, lambda: slow.append(1), done)
        if not results:
            guard = GLib.timeout_add_seconds(WAIT_S, give_up)
            loop.run()
            if results:
                GLib.source_remove(guard)
        return results, len(slow)

    def test_success_and_output(self):
        program = self.program('echo \'{"ok":true}\'\nexit 0\n')
        self.assertEqual(self.run_and_wait([program], 5), ([(0, '{"ok":true}\n')], 0))

    def test_exit_status(self):
        self.assertEqual(self.run_and_wait([self.program("exit 3\n")], 5), ([(3, "")], 0))

    def test_slow_programs_are_reported_but_never_abandoned_or_killed(self):
        marker = self.dir/"finished"
        program = self.program(f"sleep 2\ntouch '{marker}'\necho done\nexit 0\n")
        results, slow = self.run_and_wait([program], 1)
        self.assertEqual(results, [(0, "done\n")])
        self.assertEqual(slow, 1)
        self.assertTrue(marker.exists())

    def test_output_that_is_not_utf8_still_reports_the_exit(self):
        program = self.program("printf '\\377\\376'\nexit 0\n")
        results, _ = self.run_and_wait([program], 5)
        self.assertEqual(results[0][0], 0)

    def test_missing_program(self):
        self.assertEqual(self.run_and_wait([str(self.dir/"missing")], 5), ([(None, "")], 0))


if __name__ == "__main__":
    unittest.main()
