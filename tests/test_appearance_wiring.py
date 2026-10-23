"""圖示顏色即時套用：視窗與頂端列換圖示；只有一般執行才寫入使用者的圖示主題。

不建立真正的視窗：寫檔與設定視窗圖示的函式都換成記錄呼叫的假函式。
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdm.core.settings import LEGACY_DEFAULTS, Preferences
from sdm.services.app_icon import IconResult
from sdm.services.settings_store import SettingsStore
from sdm.ui import app as ui_app
from sdm.ui import appearance_wiring


class FakeTray:
    def __init__(self):
        self.icons = []

    def set_app_icon(self, icon):
        self.icons.append(icon)


def monitor(preview=None, smoke=False):
    app = ui_app.Monitor.__new__(ui_app.Monitor)
    app.preview, app.smoke, app.closing = preview, smoke, False
    app.preferences, app.settings = Preferences.defaults(), dict(LEGACY_DEFAULTS)
    app.scheduler, app.power_page = None, None
    app.tray = FakeTray()
    app.rendered = []
    app.render_indicator = lambda: app.rendered.append(True)
    app.store = SettingsStore(LEGACY_DEFAULTS, Preferences.defaults(), None, None,
                              lambda ms, callback: None, lambda source: None, persist=False)
    app.store.subscribe(app.settings_changed)
    return app


class AppIconColour(unittest.TestCase):
    def setUp(self):
        self.writes, self.windows = [], []
        patches = (patch.object(appearance_wiring.app_icon, "apply_color",
                                side_effect=lambda color: self.writes.append(color) or
                                IconResult(True, None)),
                   patch.object(appearance_wiring, "set_window_icons",
                                side_effect=self.windows.append),
                   patch.object(appearance_wiring, "rescan_icon_theme"))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_startup_applies_the_saved_colour(self):
        app = monitor()
        app.apply_app_icon()
        self.assertEqual((self.writes, self.windows), (["mint"], ["mint"]))
        self.assertTrue(app.tray.icons[-1].endswith("assets/app/mint-128.png"))

    def test_changing_the_colour_applies_it_immediately(self):
        app = monitor()
        app.store.update(app_icon_color="night")
        self.assertEqual((self.writes, self.windows), (["night"], ["night"]))
        self.assertTrue(app.tray.icons[-1].endswith("assets/app/night-128.png"))
        self.assertEqual(app.rendered, [True])

    def test_preview_and_smoke_never_write_icon_files(self):
        for options in (dict(preview="desktop"), dict(smoke=True)):
            with self.subTest(**options):
                app = monitor(**options)
                app.store.update(app_icon_color="sky")
                self.assertEqual(self.writes, [])
                self.assertEqual(self.windows[-1], "sky")

    def test_problem_is_kept_for_the_settings_window(self):
        app = monitor()
        with patch.object(appearance_wiring.app_icon, "apply_color",
                          return_value=IconResult(False, "無法寫入")):
            app.store.update(app_icon_color="peach")
        self.assertEqual(app.icon_problem, "無法寫入")

    def test_other_settings_do_not_touch_the_icon(self):
        app = monitor()
        app.store.update(tray_power=False)
        self.assertEqual((self.writes, self.windows), ([], []))


class TrayIcon(unittest.TestCase):
    def test_tray_fallback_uses_the_given_icon(self):
        from sdm.ui.tray import APP_ICON, Tray
        tray = Tray(SimpleNamespace(), SimpleNamespace())
        self.assertEqual(tray.app_icon, APP_ICON)
        tray.set_app_icon("/x/icon.png")
        self.assertEqual(tray.targets(SimpleNamespace(mode="split", visible=())),
                         {None: ("", "/x/icon.png")})


if __name__ == "__main__":
    unittest.main()
