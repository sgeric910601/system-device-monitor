"""install-dependencies.sh：原始碼安裝的套件依賴腳本。

Ubuntu 走 apt-get，指令與原本完全相同；Fedora 走 dnf；兩者都有時用 apt-get；兩者都沒有時拒絕執行。
Fedora 以有沒有 gnome-shell 判斷 GNOME（XDG_CURRENT_DESKTOP 在 SSH、文字終端機或 sudo 底下是空的），
裝好 AppIndicator 擴充功能後提示先重新登入、再啟用（Fedora 不會自動啟用）。

執行方式：PATH 只有測試的暫存資料夾，裡面的 apt-get、dnf、gnome-shell 只是讓 command -v 找到的空殼，
從不執行。sudo 換成只把參數寫進紀錄檔的 bash 函式（以 BASH_FUNC_sudo%% 匯出給腳本）：函式不是程式，
腳本裡的 sudo 不會啟動任何程序。函式沒有載入時 sudo 只會找到測試防護的假指令或找不到指令，測試一樣失敗，
絕不會執行真正的 sudo。測試不需要 root，也不安裝任何東西。
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.fixtures.helpers import ROOT

SCRIPT = ROOT/"install-dependencies.sh"
BASH = shutil.which("bash")
TIMEOUT_SECONDS = 30
LOG_ENV = "SDM_TEST_SUDO_LOG"
SUDO_FUNCTION = {"BASH_FUNC_sudo%%": f'() {{ printf "%s\\n" "$*" >> "${LOG_ENV}"; }}'}
SHELL_STUB = "#!/bin/sh\nexit 99\n"   # 只用來被 command -v 找到；被執行就是測試寫錯
APT = ("apt-get update",
       "apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gudev-1.0 "
       "gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 librsvg2-common util-linux "
       "gnome-shell-extension-appindicator")
APT_LOGITECH = "apt-get install -y solaar piper ratbagd"
DNF = ("dnf install -y python3 python3-gobject python3-cairo gtk3 libgudev "
       "libayatana-appindicator-gtk3 libnotify librsvg2 util-linux")
DNF_EXTENSION = "dnf install -y gnome-shell-extension-appindicator"
DNF_LOGITECH = "dnf install -y solaar piper libratbag-ratbagd"
RELOGIN = "log out and back in"
ENABLE = 'gnome-extensions enable "$(gnome-extensions list | grep appindicatorsupport)"'


@unittest.skipUnless(BASH, "needs bash")
class InstallDependencies(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.log = self.tmp/"sudo.log"

    def run_script(self, tools, *args, desktop=None):
        """PATH 只有 tools 這些空殼；回傳（執行結果, sudo 收到的每一行參數）。"""
        bin_dir = self.tmp/"bin"
        bin_dir.mkdir()
        for name in tools:
            (bin_dir/name).write_text(SHELL_STUB)
            (bin_dir/name).chmod(0o755)
        env = {"PATH": str(bin_dir), LOG_ENV: str(self.log), **SUDO_FUNCTION}
        if desktop is not None:
            env["XDG_CURRENT_DESKTOP"] = desktop
        result = subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True,
                                text=True, timeout=TIMEOUT_SECONDS, check=False)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, calls

    def test_ubuntu_installs_the_same_packages_as_before(self):
        result, calls = self.run_script(("apt-get",), desktop="ubuntu:GNOME")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, list(APT))

    def test_ubuntu_installs_the_extension_without_the_session_variable(self):
        result, calls = self.run_script(("apt-get",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, list(APT))
        self.assertNotIn(ENABLE, result.stdout)

    def test_ubuntu_logitech_tools(self):
        result, calls = self.run_script(("apt-get",), "--logitech")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [*APT, APT_LOGITECH])

    def test_apt_is_used_when_both_package_managers_exist(self):
        result, calls = self.run_script(("apt-get", "dnf", "gnome-shell"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, list(APT))

    def test_fedora_without_gnome_installs_only_the_base_packages(self):
        result, calls = self.run_script(("dnf",), desktop="KDE")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [DNF])
        self.assertNotIn(ENABLE, result.stdout)

    def test_fedora_gnome_is_found_without_the_session_variable(self):
        result, calls = self.run_script(("dnf", "gnome-shell"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [DNF, DNF_EXTENSION])
        self.assertIn(ENABLE, result.stdout)
        self.assertLess(result.stdout.index(RELOGIN), result.stdout.index(ENABLE))

    def test_fedora_logitech_tools(self):
        result, calls = self.run_script(("dnf",), "--logitech", desktop="KDE")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [DNF, DNF_LOGITECH])

    def test_other_distributions_are_refused(self):
        result, calls = self.run_script(())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])
        self.assertIn("Unsupported distribution", result.stderr)

    def test_unknown_option_is_refused(self):
        result, calls = self.run_script(("apt-get",), "--all")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
