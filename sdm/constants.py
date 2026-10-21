"""集中管理的間隔、逾時與上限；各模組不再各自寫死數值。

以小型類別分組，只放常數，不放邏輯。配色放在 sdm/ui/theme.py。
"""


class Collect:
    """收集器排程與外部資料來源。"""
    SYSTEM_INTERVAL = 2         # CPU／記憶體／網速／磁碟讀寫（秒）
    SLOW_INTERVAL = 5           # GPU／溫度／UPS（秒）
    DEVICES_INTERVAL = 5        # 周邊設備重新確認（秒）
    DISK_META_INTERVAL = 30     # 磁碟容量與型號（秒）
    WORKERS = 4                 # 背景收集執行緒數
    COMMAND_TIMEOUT = 3         # 外部命令預設逾時（秒）
    NVIDIA_SMI_TIMEOUT = 3
    APCACCESS_TIMEOUT = 2
    LSBLK_TIMEOUT = 3
    DBUS_TIMEOUT_MS = 1800      # 同步 D-Bus 呼叫逾時（毫秒）


class Counter:
    """累計計數器：超過此間隔沒有取樣就重新建立基準（睡眠／喚醒）。"""
    RESET_SECONDS = 15


class Solaar:
    """選用的 Solaar CLI 電量查詢。"""
    INTERVAL = 60               # 查詢間隔（秒）
    TIMEOUT = 15                # 單次查詢逾時（秒）
    MAX_AGE = 90                # 電量快取有效期（秒）


class Ui:
    """主視窗、曲線與裝置事件。"""
    EVENT_DEBOUNCE_MS = 250     # 裝置事件合併後才更新
    SERIES_MAXLEN = 151         # 曲線保留的取樣點數
    GRAPH_WINDOW_SECONDS = 300  # 曲線顯示最近 5 分鐘
    GRAPH_GAP_SECONDS = 5       # 取樣間隔超過此值時曲線斷開
    GRAPH_WIDTH = 200
    GRAPH_HEIGHT = 78
    WINDOW_WIDTH = 1040
    WINDOW_HEIGHT = 770
    DEVICE_POWER_WIDTH = 165


class Smoke:
    """--smoke-test 的時間安排。"""
    CHECK_DELAY_SECONDS = 14    # 啟動後多久開始檢查
    CAPTURE_DELAY_MS = 700      # 切換頁面後等待繪製再截圖
    DEVICES_DELAY_MS = 1400     # 總覽頁之後切到設備頁
    FINISH_DELAY_MS = 1500      # 設備頁之後寫出結果


class Launcher:
    """啟動器腳本等待專案磁碟掛載的最長秒數。"""
    WAIT_SECONDS = 60


class Packaging:
    """.deb 中 system 條目允許的安裝位置（不含開頭斜線的目錄前綴）。

    目標必須位於其中某個目錄之下；套件本身不安裝任何 /etc 檔案，
    之後若需要 /etc 設定，只能在執行期由特權小程式寫入。
    """
    SYSTEM_PREFIXES = (
        "usr/libexec/system-device-monitor",
        "usr/share/system-device-monitor",
        "usr/share/polkit-1/actions",
        "usr/lib/systemd/system",
        "usr/share/applications",
        "usr/share/icons/hicolor",
    )


class Storage:
    """設定檔寫入與保護。"""
    STALE_TEMPORARY_SECONDS = 3600  # 超過 1 小時的本程式暫存檔視為殘留，載入時清理
    BACKUP_NAME_ATTEMPTS = 100      # 同一秒內備份檔名加序號的嘗試上限
