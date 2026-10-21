"""匯入檢查腳本：在獨立子程序中匯入指定根目錄下 sdm/ 的每個模組。"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/"scripts/import_check.py"
spec = importlib.util.spec_from_file_location("import_check", SCRIPT)
import_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(import_check)


def run(root):
    return subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(root)],
                          capture_output=True, text=True, cwd=tempfile.gettempdir())


class ImportCheck(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, text in (("sdm/__init__.py", ""), ("sdm/ok.py", "VALUE = 1\n"),
                           ("sdm/sub/__init__.py", ""), ("sdm/sub/fine.py", "from sdm import ok\n")):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_module_names_follow_the_tree(self):
        self.assertEqual(import_check.module_names(self.root),
                         ["sdm", "sdm.ok", "sdm.sub", "sdm.sub.fine"])

    def test_synthetic_tree_imports_cleanly(self):
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("4 modules", result.stdout)

    def test_failures_are_listed_with_non_zero_exit(self):
        (self.root/"sdm/broken.py").write_text("import module_that_does_not_exist\n")
        (self.root/"sdm/sub/raises.py").write_text("raise RuntimeError('synthetic')\n")
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("sdm.broken: ModuleNotFoundError", result.stdout)
        self.assertIn("sdm.sub.raises: RuntimeError", result.stdout)

    def test_missing_package_is_an_error(self):
        result = run(self.root/"elsewhere")
        self.assertNotEqual(result.returncode, 0)

    def test_project_tree_imports_cleanly(self):
        result = run(ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
