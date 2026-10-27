"""看板的一鍵更新（Monitor 的混入類別）：建立更新服務、通知設定視窗、送出更新通知與安裝後重新啟動。

- 模擬測試（--smoke-test）完全不建立更新服務；示範模式（--preview）只用 update_demo 的假狀態。
  兩者都不建立網路用戶端，也不讀寫 update-state.json。
- 一般執行：GitHub 用戶端與下載驗證在專用的工作執行緒，結果以 GLib.idle_add 回到主執行緒；
  計時器用 GLib.timeout_add_seconds。程式所在位置是 /usr/share/system-device-monitor 時視為 .deb
  安裝（只有 .deb 會放在那裡），其他都是原始碼資料夾。
- 自動檢查發現新的正式版時，經由通知服務送出一則通知（點通知開啟「設定 → 更新」）。
- 安裝成功後記下重新啟動的參數（看板原本在背景執行時加上 --background）並結束看板；
  app.run() 在主迴圈結束後以 os.execv 執行啟動器，讓同一個程序載入新版本的檔案。不另外啟動新程序
  再結束：單一實例的新程序若在舊程序釋放名稱之前註冊，會把請求交給正在結束的舊程序然後離開。
  重新啟動前用到的模組都在開啟程式時就已載入（套件檔案已被取代，之後不能再延後載入）。
"""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import sys
import time

from gi.repository import GLib

from sdm import paths
from sdm.constants import Update
from sdm.core.update_logic import AppVersion
from sdm.data.github import GitHubClient
from sdm.services import update_install
from sdm.services.update_demo import demo_updater
from sdm.services.update_runner import PatientRunner
from sdm.services.updater import (KIND_DEB, KIND_SOURCE, Hooks, Services, Updater,
                                  UpdateMemory, remove_directory)
from sdm.ui.update_text import update_alert

LOG = logging.getLogger(__name__)
BACKGROUND = "--background"


def install_kind(root):
    return KIND_DEB if Path(root) == Path(Update.PACKAGE_ROOT) else KIND_SOURCE


def restart_argv(background):
    return [Update.LAUNCHER] + ([BACKGROUND] if background else [])


def restart(argv, execv=os.execv):
    """以啟動器取代目前的程序；失敗時記錄並回傳 False（看板照常結束）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    try:
        execv(argv[0], argv)
    except OSError as error:
        LOG.error("Cannot restart after the update: %s", type(error).__name__)
        return False
    return True


def schedule_seconds(seconds, callback):
    return GLib.timeout_add_seconds(seconds, callback)


def remove_source(source):
    if GLib.MainContext.default().find_source_by_id(source):
        GLib.source_remove(source)


def idle(function, *args):
    GLib.idle_add(function, *args)


class UpdateMixin:
    updater = None
    restart_argv = None             # 安裝更新後 app.run() 要 exec 的參數
    update_pool = None

    def start_updates(self):
        if self.preview:
            self.updater = demo_updater(self.preview, self.update_changed, paths.VERSION)
        elif not self.smoke:
            self.updater = self.production_updater()
            self.updater.start()

    def production_updater(self):
        client = GitHubClient(paths.VERSION)
        self.update_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="update")

        def prepare(release):
            workdir = update_install.private_workdir(os.environ, os.getuid())
            try:
                installed = update_install.installed_version(paths.VERSION)
                return update_install.prepare(release, client, workdir, Update.PUBLIC_KEYS,
                                              installed)
            except BaseException:
                remove_directory(workdir)
                raise
        services = Services(fetch=lambda: client.get(Update.RELEASES_API, Update.API_MAX_BYTES),
                            prepare=prepare, submit=self.update_pool.submit, on_main=idle,
                            schedule=schedule_seconds, cancel=remove_source,
                            runner=PatientRunner(), clock=time.time, cleanup=remove_directory)
        hooks = Hooks(on_change=self.update_changed, notify=self.update_notify,
                      installed=self.update_installed, preferences=lambda: self.preferences)
        return Updater(services, hooks, AppVersion.current(paths.VERSION), install_kind(paths.ROOT),
                       memory=UpdateMemory(paths.STATE/Update.STATE_FILE), root=paths.ROOT)

    def update_changed(self):
        window = getattr(self, "settings_window", None)
        if window is not None:
            window.update_changed()

    def update_notify(self, release):
        """自動檢查發現新的正式版：下一次主迴圈才送出（送出可能要等通知服務回應）。"""
        if self.notifier is not None:
            GLib.idle_add(self.send_update_alert, update_alert(release))

    def send_update_alert(self, alert):
        self.notifier.send(alert)
        return False

    def update_installed(self):
        self.restart_argv = restart_argv(background=not self.window_shown())
        self.stop()

    def stop_updates(self):
        if self.updater is not None:
            self.updater.stop()
        if self.update_pool is not None:
            self.update_pool.shutdown(wait=False, cancel_futures=True)
