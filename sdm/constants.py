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
    APCACCESS = "/sbin/apcaccess"
    NVIDIA_SMI = "/usr/bin/nvidia-smi"


class Ups:
    """apcaccess 數值的合理範圍；超出範圍視為讀不到。"""
    LOAD_MAX_PCT = 150          # 負載百分比上限（過載時可能超過 100）
    CHARGE_MAX_PCT = 100
    NOMINAL_MIN_W = 1           # 額定功率範圍（瓦）
    NOMINAL_MAX_W = 20000
    TIME_LEFT_MAX_MIN = 10000   # 預估續航上限（分鐘）
    LINE_V_MAX = 400            # 市電電壓上限（伏特）
    MODEL_MAX_CHARS = 64


class Gpu:
    """顯示卡讀取：NVIDIA 常駐串流與 AMD sysfs。"""
    STREAM_INTERVAL_MS = Collect.SLOW_INTERVAL * 1000   # nvidia-smi -lms 間隔
    BACKOFF_INITIAL = 1         # 串流程序結束後重新啟動的等待（秒），每次加倍
    BACKOFF_MAX = 60
    BACKOFF_RESET_SECONDS = 60  # 程序持續執行超過此秒數，下次重啟從最短等待算起
    STOP_TIMEOUT = 2            # 停止時等待程序與執行緒結束的總秒數（介面最多卡住這麼久）
    TERM_GRACE_RATIO = 0.5      # 總時限中留給 SIGTERM 的比例，之後改送 SIGKILL
    MAX_SILENT_FAILURES = 5     # 連續幾次很快結束且沒有任何輸出，就停止重啟
    NAME_MAX_CHARS = 64
    TEMP_MIN_C = -40            # 溫度合理範圍
    TEMP_MAX_C = 150
    INTEGRATED_VRAM_MAX = 2 * 1024 ** 3   # 專用顯示記憶體不超過 2 GiB 視為內建顯示
    AMD_VENDOR = "0x1002"
    PWM_MAX = 255               # pwm1_max 不存在時的預設上限


class Power:
    """整機耗電、元件拆解與平均。"""
    EMA_TAU_SECONDS = 180       # 平均的時間常數，約等於「近 3 分鐘平均」
    STALE_FACTOR = 3            # 元件樣本超過「慢速間隔 × 此倍數」視為過期
    SYSTEM_MAX_W = 2000         # 整機耗電合理上限（不含）
    COMPONENT_MAX_W = 1000      # 單一元件合理上限（不含）


class Battery:
    """筆電電池：sysfs power_supply 讀值範圍，以及沿用參考專案的判斷門檻。"""
    SUPPLY_DIR = "sys/class/power_supply"
    TEXT_MAX_CHARS = 64         # 廠商、型號等文字
    STATUS_MAX_CHARS = 32
    MAX_WH = 1000               # 能量讀值上限（瓦時）
    MAX_AH = 100                # 電量讀值上限（安培小時）
    MAX_V = 60                  # 電壓上限（伏特，含 USB-PD 48 V）
    MAX_A = 20                  # 電流上限（安培）
    MAX_W = 500                 # 功率上限（瓦，與參考專案 applyLivePower 相同）
    TEMP_MIN_C = -40
    TEMP_MAX_C = 100
    CYCLES_MAX = 100000
    CHARGING_MIN_W = 3.0        # 充入電池至少 3 W 才算充電（參考 chargingNow）
    IDLE_MAX_W = 0.5            # 低於 0.5 W 不算充電；介於兩者之間看核心的狀態旗標
    DRAIN_W = -0.5              # 接電時電池功率低於此值，視為電池仍在放電
    DRAIN_A = -0.05             # 沒有功率讀值時改看電流（參考專案的 -50 mA）
    LIMIT_MARGIN = 1            # 電量 ≥ 上限 − 1 視為已達上限
    FULL_PERCENT = 100
    FULL_RELEASE_PERCENT = 98   # 已充飽的狀態維持到電量低於 98%
    CONFIRM_SECONDS = 10        # 接電中的狀態變化要持續 10 秒才切換顯示
    FLOW_HOLD_W = 2.0           # 接電未充電時 ±2 W 內視為由變壓器直接供電
    FLOW_ACTIVE_W = 0.05
    FLOW_SWING_W = 5.0          # 方向明顯相反（超過 5 W）時立即切換流向圖
    JUST_PLUGGED_SECONDS = 30   # 剛插電 30 秒內電池仍在放電不算供電不足
    CHARGE_WARMUP_SECONDS = 60  # 開始充電後頭 60 秒的電流不納入平均
    LIVE_MIN_W = 0.1            # 平均功率或電流超過此值才推算剩餘時間
    LIVE_MIN_A = 0.010
    RESERVE_PERCENT = 10        # 「到 10%」的保留電量
    RECAP_GAP_SECONDS = 30 * 60  # 今日回顧：兩筆紀錄間隔最多算 30 分鐘
    REFRESH_DELAYS = (1, 3, 6)  # 插拔電源後再重新讀取的時間（秒），與參考專案相同


class History:
    """耗電與電量歷史（XDG 資料目錄下的 history.json）。"""
    FILE_NAME = "history.json"
    FORMAT = 1
    DIRECTORY_MODE = 0o700              # 本程式建立的資料夾
    FILE_MODE = 0o600                   # history.json 與其備份只有本人可讀寫
    MAX_FILE_BYTES = 20 * 1024 * 1024   # 超過就不讀取（一週約 0.5 MB），先備份再從空白開始
    RETENTION_SECONDS = 7 * 24 * 3600
    SAVE_EVERY = 15                     # 每累積幾筆新紀錄寫檔一次
    SAVE_RETRY_SECONDS = (15 * 60, 30 * 60, 60 * 60)   # 寫檔失敗後的重試間隔，之後維持最後一個
    SAVE_MAX_FAILURES = 5               # 連續失敗幾次就在這次執行放棄寫檔
    RECORD_INTERVAL_SECONDS = 60        # 桌機每分鐘記錄一次
    FUTURE_TOLERANCE_SECONDS = 300      # 載入時容許的未來時間（時鐘誤差）
    MAX_POINTS = 240                    # 圖表降採樣後的最多點數
    GAP_SECONDS = 6 * 3600              # 超過此間隔沒有資料時曲線斷開
    WATTS_GAP_SECONDS = 600             # 耗電曲線的斷開間隔
    UNPLUGGED_MAX_AGE_SECONDS = 600     # 最後一筆紀錄超過此秒數就不推算拔電時間
    CLOCK_JUMP_WINDOW_SECONDS = 24 * 3600   # 修剪會刪到既有最新一筆前這段時間內的資料，視為時鐘往前跳
    RANGES = (3600, 24 * 3600, 7 * 24 * 3600)   # 圖表範圍：1 小時、24 小時、7 天


class Chart:
    """耗電歷史圖。"""
    REFERENCE_AXIS_MINIMUM = 1000   # 參考專案 niceAxisMax 的預設下限
    WATTS_AXIS_MINIMUM = 10         # 耗電圖 Y 軸上限至少 10 W
    AXIS_MARGIN_RATIO = 0.07        # 太靠近左右邊緣的時間刻度不畫（標籤會被切掉）
    HEIGHT = 180
    MARGIN_LEFT = 58                # 左側瓦數標籤
    MARGIN_RIGHT = 46               # 右側不斷電系統電量標籤
    MARGIN_TOP = 10
    MARGIN_BOTTOM = 24              # 下方時間標籤
    WATTS_LINE_WIDTH = 2.0
    UPS_LINE_WIDTH = 1.5


class Flow:
    """電力流向圖（靜態，無動畫）。"""
    PADDING = 8
    SOURCE_WIDTH = 92
    TOTAL_WIDTH = 104
    ITEM_WIDTH = 180
    NODE_HEIGHT = 96                # 來源與整機節點的高度上限
    ITEM_HEIGHT = 44
    ITEM_GAP = 8
    MIN_HEIGHT = 140
    BAND_MIN = 2                    # 流帶最細 2 px
    BAND_MAX = 44                   # 流帶最粗（整機）
    MIN_WIDTH = 420                 # 兩欄排列時每張卡片的最小內容寬度


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
    CAPTURE_DELAY_MS = 700      # 切換頁面後等待繪製再確認與截圖
    PAGE_DELAY_MS = 700         # 截圖後再切到下一頁
    FINISH_DELAY_MS = 800       # 最後一頁之後寫出結果


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
