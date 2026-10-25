"""示範模式（--preview）的更新分類：固定的假狀態，不連網路、不寫任何檔案、不執行任何程式。

各情境：桌機示範＝.deb 有新版本可以安裝；筆電用電池＝已是最新版本；筆電充電中＝有新版本但
這個版本沒有設定簽章金鑰；筆電到上限＝原始碼資料夾有新版本（顯示 git 指令）。另有「檢查失敗」
（只給截圖工具用）。新版本一律是目前版本的下一個次版本，說明文字標明是示範。
"""
from dataclasses import replace
from pathlib import Path
import time

from sdm.constants import Update
from sdm.core.update_logic import AppVersion, ReleaseInfo, asset_name, tag
from sdm.services.updater import (AVAILABLE, BUSY, FAILED, KIND_DEB, KIND_SOURCE, LATEST,
                                  UpdateState)

DEMO_ROOT = Path("/opt/system-device-monitor")     # 示範的原始碼位置（不是這台電腦的）
SCENARIO_VARIANTS = {"desktop": "available", "laptop-battery": "latest",
                     "laptop-charging": "no-keys", "laptop-limit": "source"}
DEMO_NOTES = ("## 新增\n- 示範：設定 → 更新可以一鍵安裝已簽章的新版本\n"
              "- 示範：安裝前先驗證發布簽章與檢查碼\n## 修正\n- 示範：其他改進")
DEMO_ERROR = "檢查失敗：無法連線到 GitHub"
DEMO_INSTALL = "安裝失敗：示範模式不會安裝更新。"
DEMO_AGE_SECONDS = 2 * 3600                         # 示範的「上次檢查」：兩小時前


def demo_release(current):
    numbers = tuple(current.numbers) + (0,) * (Update.VERSION_PARTS - len(current.numbers))
    version = AppVersion((numbers[0], numbers[1] + 1, 0))
    name = tag(version)
    page = f"{Update.RELEASES_PAGE}/tag/{name}"
    return ReleaseInfo(tag=name, numbers=version.numbers, page_url=page,
                       asset_name=asset_name(version), asset_url=page, asset_size=1,
                       checksum_url=page, signature_url=page, notes=DEMO_NOTES)


class DemoUpdater:
    """與 sdm.services.updater.Updater 相同的介面，狀態只在記憶體中。"""

    def __init__(self, variant, on_change, version_text, clock=time.time):
        self.on_change, self.clock = on_change, clock
        self.current = AppVersion.current(version_text)
        self.kind = KIND_SOURCE if variant == "source" else KIND_DEB
        self.keys_configured = variant != "no-keys"
        self.root = DEMO_ROOT
        self.variant = variant
        self.state = self.variant_state(clock() - DEMO_AGE_SECONDS)

    def variant_state(self, stamp):
        if self.variant == "latest":
            return UpdateState(phase=LATEST, last_check=stamp)
        if self.variant == "failed":
            return UpdateState(phase=FAILED, error=DEMO_ERROR)
        return UpdateState(phase=AVAILABLE, release=demo_release(self.current), last_check=stamp)

    @property
    def busy(self):
        return self.state.phase in BUSY

    @property
    def installable(self):
        return self.kind == KIND_DEB and self.keys_configured and self.state.phase == AVAILABLE

    def update(self, state):
        self.state = state
        self.on_change()

    def check_now(self):
        self.update(self.variant_state(self.clock()))
        return True

    def install(self):
        self.update(replace(self.state, error=DEMO_INSTALL))
        return False

    def start(self):
        return None

    def stop(self):
        return None


def demo_updater(scenario, on_change, version_text):
    return DemoUpdater(SCENARIO_VARIANTS.get(scenario, "latest"), on_change, version_text)
