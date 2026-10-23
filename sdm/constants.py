"""集中管理的間隔、逾時與上限；各模組不再各自寫死數值。

以小型類別分組，只放常數，不放邏輯。配色放在 sdm/ui/theme.py。
"""


class App:
    """應用程式代號：單一實例與 --test-notification 的遠端動作都用它。"""
    ID = "tw.local.SystemDeviceMonitor"


class Collect:
    """收集器排程與外部資料來源。"""
    SYSTEM_INTERVAL = 2         # CPU／記憶體／網速／磁碟讀寫（秒）
    SLOW_INTERVAL = 5           # GPU／溫度／UPS（秒）
    DEVICES_INTERVAL = 5        # 周邊設備重新確認（秒）
    WORKERS = 4                 # 背景收集執行緒數
    COMMAND_TIMEOUT = 3         # 外部命令預設逾時（秒）
    NVIDIA_SMI_TIMEOUT = 3
    APCACCESS_TIMEOUT = 2
    LSBLK_TIMEOUT = 3
    DBUS_TIMEOUT_MS = 1800      # 同步 D-Bus 呼叫逾時（毫秒）
    APCACCESS = "/sbin/apcaccess"
    NVIDIA_SMI = "/usr/bin/nvidia-smi"


class Schedule:
    """更新頻率與省電模式（設定 → 一般）：各取樣層由此算出間隔。"""
    INTERVALS = (1, 2, 5, 10)   # 可選的更新頻率（秒）
    DEFAULT_INTERVAL = 2
    SAVING_MIN_INTERVAL = 2     # 省電模式生效時至少每 2 秒
    NVIDIA_MIN_MS = 2000        # nvidia-smi -lms 的最短間隔（毫秒）
    MILLISECONDS = 1000


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
    STREAM_INTERVAL_MS = 2000   # nvidia-smi -lms 預設間隔（更新頻率 2 秒）；之後依設定調整
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


class CpuPower:
    """選用的管理員 CPU 功耗取樣（RAPL）：元件位置、socket 與通訊協定。

    helper/ 底下的小程式以 root 執行、不載入本套件，所以各自保留同樣的值；
    tests/test_system_units 等測試確認兩邊一致。
    """
    PROTOCOL = 1
    EXIT_CODES = (0, 2, 3, 4)   # 成功、參數錯誤、執行失敗、沒有權限
    DEB_HELPER_DIR = "/usr/libexec/system-device-monitor"
    LOCAL_HELPER_DIR = "/usr/local/libexec/system-device-monitor"
    DEB_HELPER = DEB_HELPER_DIR + "/sdm-cpu-power"                 # pkexec 執行的小程式
    LOCAL_HELPER = LOCAL_HELPER_DIR + "/sdm-cpu-power"
    ACTION_DEB = "tw.local.system-device-monitor.cpu-power"         # polkit 動作
    ACTION_LOCAL = "tw.local.system-device-monitor.local.cpu-power"
    DEB_SERVICE_HELPER = DEB_HELPER_DIR + "/sdm-cpu-power-service"
    LOCAL_SERVICE_HELPER = LOCAL_HELPER_DIR + "/sdm-cpu-power-service"
    PKEXEC = "/usr/bin/pkexec"
    PKEXEC_TIMEOUT = 120        # 等待授權與小程式完成的上限（秒）
    PROTOCOL_TIMEOUT = 5        # 「小程式 --protocol」的逾時（秒）
    DEB_SOCKET = "/run/system-device-monitor/cpu-power.sock"
    LOCAL_SOCKET = "/run/system-device-monitor/cpu-power-local.sock"
    REQUEST = b"r\n"
    MIN_SPACING = 1.0           # 服務端兩次計算至少相隔 1 秒（秒）
    MAX_WINDOW = 30.0           # 兩次計算最多相隔 30 秒，超過就重新建立基準
    MAX_CLIENTS = 8
    IDLE_EXIT = 30.0            # 服務沒有連線多久後結束（秒）
    CLIENT_IDLE = 30.0          # 連線多久沒有請求就被關閉（秒）
    WATTS_DIGITS = 1            # 回傳的瓦數四捨五入到 0.1 W
    SOCKET_TIMEOUT = 1.0        # 客戶端連線與讀取回應的逾時（秒）
    MAX_REPLY_BYTES = 256
    BACKOFF_INITIAL = 1         # 客戶端失敗後重試的等待（秒），每次加倍
    BACKOFF_MAX = 60
    PPT_SCALE = 1000            # AMD APU 的 PPT 原始值 ÷ 1000 ＝ 瓦（需要校正確認）
    CALIBRATION_SAMPLES = 30    # 每 30 筆同時有 RAPL 與 PPT 的取樣判斷一次
    CALIBRATION_TOLERANCE = 0.25    # 比例中位數在 ±25% 內才確認
    CALIBRATION_MIN_W = 1.0     # RAPL 低於 1 W 時不比對（比例不穩定）
    CALIBRATION_MAX_W = 1000    # 與 Power.COMPONENT_MAX_W 相同（不含）
    CALIBRATION_FILE = "cpu-calibration.json"   # XDG 狀態目錄


class Supply:
    """sysfs power_supply 的類型與範圍名稱（核心定義的固定字串）。"""
    TYPE_BATTERY = "Battery"
    TYPE_USB = "USB"
    ADAPTER_TYPES = ("Mains", "USB")
    SCOPE_DEVICE = "Device"         # 滑鼠、鍵盤等周邊自己的電池


class Battery:
    """筆電電池：sysfs power_supply 讀值範圍，以及沿用參考專案的判斷門檻。"""
    SUPPLY_DIR = "sys/class/power_supply"
    PERCENT = 100               # 百分比的滿刻度
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
    ICON_LEVEL_STEP = 10        # 電池圖示以 10% 為一級（與 GNOME Shell 相同）
    FULL_PERCENT = 100
    FULL_RELEASE_PERCENT = 98   # 已充飽的狀態維持到電量低於 98%
    CONFIRM_SECONDS = 10        # 接電中的狀態變化要持續 10 秒才切換顯示
    FLOW_HOLD_W = 2.0           # 接電未充電時 ±2 W 內視為由變壓器直接供電
    FLOW_ACTIVE_W = 0.05
    FLOW_SWING_W = 5.0          # 方向明顯相反（超過 5 W）時立即切換流向圖
    JUST_PLUGGED_SECONDS = 30   # 剛插電 30 秒內電池仍在放電不算供電不足
    CHARGE_WARMUP_SECONDS = 60  # 開始充電後頭 60 秒的電流不納入平均
    UNIT_CHANGE_READS = 2       # 推算單位（W／A）要連續幾次改變才切換並重算平均
    LIVE_MIN_W = 0.1            # 平均功率或電流超過此值才推算剩餘時間
    LIVE_MIN_A = 0.010
    RESERVE_PERCENT = 10        # 「到 10%」的保留電量
    RECAP_GAP_SECONDS = 30 * 60  # 今日回顧：兩筆紀錄間隔最多算 30 分鐘
    REFRESH_DELAYS = (1, 3, 6)  # 插拔電源後再重新讀取的時間（秒），與參考專案相同
    TIME_MAX_SECONDS = 10 * 24 * 3600   # UPower 剩餘時間的合理上限
    UPOWER_BACKOFF_SECONDS = 30  # UPower 或電源模式服務逾時後，這段時間內不再呼叫
    UPOWER_STALL_SECONDS = 120  # UPower 剩餘或充滿時間一直是 0 超過此秒數，視為不會給出預估
    MODE_SWITCH_EMPTY_READS = 3  # 連續幾次讀不到系統電池才改回桌機模式（避免驅動重新列舉時閃動）
    UPOWER_CHARGING = 1         # UPower 裝置狀態：充電中
    UPOWER_DISCHARGING = 2      # UPower 裝置狀態：放電中
    PROFILE_MAX_CHARS = 32


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
    """電力流向圖：流帶上的虛線依瓦數流動（看得到且沒有省電時），否則畫靜態箭頭。"""
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
    MIN_WIDTH = 360                 # 卡片的最小內容寬度（480 px 寬的視窗也放得下）
    NODE_GAP = 12                   # 寬度不夠時節點之間至少保留的空隙
    NODE_MIN_SCALE = 0.6            # 節點寬度最多縮到原本的 60%
    FRAME_MS = 67                   # 動畫每格間隔：每秒最多 15 格
    DASH_LENGTH = 6                 # 流動虛線的線段與間隔（px）
    DASH_GAP = 10
    DASH_PERIOD = DASH_LENGTH + DASH_GAP
    DASH_SPEED_MIN = 8              # 流速（px／秒）＝最慢＋係數 × √W，最多 80
    DASH_SPEED_PER_SQRT_W = 3
    DASH_SPEED_MAX = 80
    ARROW_SIZE = 7                  # 靜態箭頭的大小（px）


class Counter:
    """累計計數器：超過此間隔沒有取樣就重新建立基準（睡眠／喚醒）。"""
    RESET_SECONDS = 15


class Monitor:
    """系統監控頁（CPU／GPU、記憶體、磁碟、網路）：曲線、排行與只在頁面顯示時才做的取樣。"""
    HISTORY_CAPACITY = 120          # 曲線最多保留的點數（參考專案 MonitorHistory）
    HISTORY_WINDOW_SECONDS = 120    # 曲線只保留最近 2 分鐘
    DETAIL_INTERVAL = 2             # 頁面顯示時的詳細取樣間隔（秒）
    TOP_APPS = 5                    # App 排行的筆數
    RATE_AXIS_MINIMUM = 1000        # 速率曲線 Y 軸上限至少 1 KB/s


class Memory:
    """記憶體壓力分級：PSI 的 10 秒平均（百分比）或可用記憶體比例，任一項達到門檻就升級。"""
    WARN_SOME_AVG10 = 10.0          # 有程序等待記憶體的時間比例
    CRITICAL_FULL_AVG10 = 5.0       # 所有程序都在等待記憶體的時間比例
    WARN_AVAILABLE_FRACTION = 0.10
    CRITICAL_AVAILABLE_FRACTION = 0.05
    KILOBYTE = 1024                 # /proc/meminfo 的 kB 其實是 KiB


class Thermal:
    """溫度分組與風扇：合理範圍（不含兩端）、每個感測器沿用最近有效值的時間。"""
    MIN_C = 0                       # 0 °C 以下視為讀取失敗
    MAX_C = 125                     # 125 °C 以上視為讀取失敗
    HOLD_SECONDS = 120              # 讀不到時沿用最近一次有效值的最長時間（參考專案）
    INTERVAL = 5                    # CPU／GPU 頁顯示時讀取溫度與風扇的間隔（秒）


class Disk:
    """磁碟頁：容量長條變色、健康度分級、SMART 讀取頻率與 lsblk 更新間隔。"""
    BAR_CRITICAL = 0.9              # 已用超過 90% 顯示紅色
    BAR_WARNING = 0.8               # 超過 80% 顯示橘色
    HEALTH_GOOD = 70                # 健康度 ≥ 70% 綠色，≥ 30% 橘色，其他紅色
    HEALTH_FAIR = 30
    DAYS_FROM_HOURS = 48            # 通電時間達 48 小時才加上「約幾天」
    MODEL_MAX_CHARS = 64
    BLOCK_NAME_MAX_CHARS = 32
    KELVIN_OFFSET = 273.15
    META_VISIBLE_INTERVAL = 30      # 總覽或磁碟頁顯示時，lsblk 每 30 秒更新
    META_HIDDEN_INTERVAL = 300      # 其他時候每 5 分鐘
    SMART_PROPERTIES_INTERVAL = 60  # UDisks2 SMART 屬性
    SMART_ATTRIBUTES_INTERVAL = 600  # NVMe 的 SmartGetAttributes（出廠以來讀寫量、已用壽命）
    SMART_BACKOFF_SECONDS = 30      # 任何一個呼叫逾時後，這段時間內不再呼叫


class Network:
    """網路頁：主要介面的 IPv4（socket ioctl，不執行外部命令）。"""
    IPV4_INTERVAL = 30              # IPv4 每 30 秒或主要介面改變時重新讀取
    IPV4_RETRY_SECONDS = 5          # 讀不到位址時（例如還在取得 DHCP）5 秒後再試
    SIOCGIFADDR = 0x8915            # Linux 的「取得介面位址」ioctl
    IFNAMSIZ = 16                   # 介面名稱含結尾 NUL 的長度上限
    IFREQ_BYTES = 256
    ADDRESS_OFFSET = 20             # 回應中 IPv4 位址的位置（名稱 16＋family 2＋port 2）
    ROUTE_UP = 0x1                  # /proc/net/route 的 RTF_UP


class Alerts:
    """通知：遲滯、啟動與喚醒後只記錄狀態、頻率限制（規則沿用參考專案 Notifier）。"""
    RESUME_GAP_SECONDS = 120    # 兩次取樣相隔超過 2 分鐘（含睡眠時間）視為剛喚醒，只記錄狀態
    REARM_MARGIN = 2            # 低電量：回到門檻＋2% 以上才重新啟用
    FULL_REARM_MARGIN = 5       # 充飽：低於 min(門檻, 充電上限) − 5% 才重新啟用
    FULL_PERCENT = 100          # 沒有充電上限時的目標；充飽門檻 100% 時內文寫「已充飽」
    UPS_CONFIRM_SAMPLES = 2     # 不斷電系統連續兩次取樣都在用電池才通知
    RATE_LIMIT_SECONDS = 60     # 同一種通知（周邊為同一台設備）每 60 秒最多一則
    SEND_RETRY_SECONDS = 30     # 送出失敗（例如通知服務還沒啟動）後多久再試
    SEND_MAX_ATTEMPTS = 5       # 同一則通知最多試幾次
    PERIPHERAL_DEFAULT = 20     # 周邊設備沒有設定時的低電量門檻
    LEVEL_ONLY_MIN_THRESHOLD = 10   # 只回報電量等級的設備：門檻至少 10% 才依「低電量」等級通知
    NAME_MAX_CHARS = 64         # 通知中的設備名稱長度上限
    MAX_LIVE = 32               # 最多同時保留幾則通知（點通知時才找得到要開的頁面）
    SLOW_SHOW_SECONDS = 2       # 送出一則通知超過這麼久，視為通知服務太慢
    SLOW_PAUSE_SECONDS = 60     # 通知服務太慢時暫停送出的時間
    APP_NAME = "系統與裝置監看"
    DESKTOP_ENTRY = "system-device-monitor"
    TEST_ACTION = "test-notification"   # 正在執行的看板提供的動作（--test-notification 使用）
    REMOTE_TEST_SECONDS = 10    # 從其他程序要求的測試通知每 10 秒最多一則
    BUS_NAME = "org.freedesktop.Notifications"
    PROBE_TIMEOUT_MS = 1500     # 確認通知服務是否存在的 D-Bus 呼叫逾時


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
    SETTINGS_WIDTH = 760             # 設定視窗的預設大小
    SETTINGS_HEIGHT = 620
    NARROW_WIDTH = 640               # 視窗比這窄時側欄收起，改用標題列的頁面選單
    SIDEBAR_WIDTH = 168
    ICON_RETRY_SECONDS = 60          # 頂端列圖示寫不出來時，同樣的內容隔這麼久才再試
    DEVICE_POWER_WIDTH = 165


class Smoke:
    """--smoke-test 的時間安排。"""
    CHECK_DELAY_SECONDS = 14    # 啟動後多久開始檢查
    CAPTURE_DELAY_MS = 700      # 切換頁面後等待繪製再確認與截圖
    PAGE_DELAY_MS = 700         # 截圖後再切到下一頁
    FINISH_DELAY_MS = 800       # 最後一頁之後寫出結果
    SETTLE_SECONDS = 0.3        # --capture：捲動後等畫面重畫完再截圖
    SETTLE_STEP_SECONDS = 0.01


class Preview:
    """--preview 示範模式。"""
    SCENARIOS = ("laptop-battery", "laptop-charging", "laptop-limit", "desktop")
    APP_ID_SUFFIX = ".Preview"          # 與正在執行的看板分開，不會把請求交給它
    CHECK_DELAY_SECONDS = 3             # 示範資料已在記憶體，模擬測試不用等收集器


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
    SAVE_DELAY_MS = 500             # 設定變更後延遲寫檔（連續調整只寫一次）
    SAVE_RETRY_SECONDS = (15, 60)   # 設定寫檔失敗後的重試間隔；再失敗就在這次執行放棄
    MILLISECONDS = 1000
