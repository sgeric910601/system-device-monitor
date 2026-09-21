# 系統與裝置監看 · System Device Monitor

Ubuntu 桌面上的即時系統資源與周邊電量看板。提供彩色頂端列、完整視窗、
最近五分鐘曲線，以及目前連線中的 USB／藍牙設備。

A local GTK 3 system and peripheral battery monitor for Ubuntu.
No telemetry, accounts, or cloud service required.

## 功能

- 頂端列順序：CPU（藍）→ CPU 溫度（橘）→ RAM（綠）→ GPU（紫）→ 網路（青）。
- 完整看板：系統負載、磁碟容量與讀寫速度、實體與虛擬網路、周邊電量、APC UPS。
- 溫度放在對應裝置名稱後；硬碟格式為「裝置 · 型號 · 容量 · 溫度」。
  依 sysfs 硬體路徑配對，沒有資料時標示「溫度未回報」。
- 電量有百分比就顯示百分比；只有等級時明確顯示「電量等級：正常」等文字，
  不把等級換成虛構百分比。
- 頂端列選單的「設備電量」每台設備一行，只列出有回報電量的設備；UPS 另有專屬一行。
- 關閉視窗後繼續在頂端列運作；選單「退出」才停止。
- 首次啟動啟用登入自啟，可在「設定」關閉；更新保留既有選擇。

## 安裝：Ubuntu 套件

支援驗證目標：Ubuntu 24.04、GNOME 46、系統 Python 3.12。
GNOME 必須啟用 AppIndicator 擴充功能才能顯示頂端列。

從 [Releases](https://github.com/sgeric910601/system-device-monitor/releases)
下載 .deb 與 SHA256SUMS，在下載目錄執行：

    sha256sum --ignore-missing -c SHA256SUMS
    sudo apt install ./system-device-monitor_0.1.0_all.deb

在應用程式選單搜尋「系統與裝置監看」，或執行：

    system-device-monitor

若先前使用原始碼安裝，先以原 checkout 的 install.py --uninstall 移除使用者啟動器，
再安裝 .deb，以免兩種啟動器重疊。此操作保留設定；轉換後在設定重新開啟自啟。

## 安裝：原始碼

    git clone https://github.com/sgeric910601/system-device-monitor.git
    cd system-device-monitor
    ./install-dependencies.sh
    /usr/bin/python3 install.py
    ~/.local/bin/system-device-monitor

安裝與執行使用 Ubuntu 系統 Python；不要用 Conda 的 Python 代替。
只有安裝 apt 相依套件需要 sudo，install.py 與看板本身以一般使用者執行。
原始碼可放任意目錄；登入啟動器會等待專案磁碟最多 60 秒。
移動 checkout 後重新執行 install.py 即可更新啟動器。

## 使用

點擊任一頂端列項目可開啟完整看板、設備頁或設定。
「設定」可選擇頂端列項目及登入自啟；UPS 詳細資料在看板中，
不額外建立 UPS 頂端列圖示。

    system-device-monitor --background
    system-device-monitor --version

CPU／記憶體／網速／磁碟讀寫每 2 秒更新；GPU／溫度／UPS 每 5 秒，
磁碟容量每 30 秒。裝置事件觸發更新，另有每 5 秒重新確認。
Solaar 電量查詢每 60 秒執行一次，獨立背景工作不阻塞裝置清單。
網速摘要加總已連線實體介面，虛擬介面另外列出。

設定與日誌遵循 XDG 路徑，預設：
- 設定：~/.config/system-device-monitor/settings.json
- 選用裝置別名：~/.config/system-device-monitor/device-aliases.json
- 自啟：~/.config/autostart/system-device-monitor.desktop
- 日誌：~/.cache/system-device-monitor/app.log

## 硬體支援與限制

- CPU 型號／核心數自動讀取；CPU 溫度支援 k10temp 的 Tctl／Tdie，
  以及 coretemp 的 Package 溫度。其他感測器仍列在看板。
- GPU 詳細使用率、VRAM、功耗與風扇百分比來自 nvidia-smi；
  多 NVIDIA GPU 時顯示驅動列出的第一張。其他 GPU 的 hwmon 溫度可能可用，
  但首版不提供其使用率。
- 硬碟溫度使用 hwmon。沒有 hwmon 的 SATA 硬碟／USB 外接盒會顯示未回報；
  首版不執行需要管理員權限的 SMART 查詢。
- G304／G305（配對裝置 ID 4074）可透過選用的 Solaar 查詢裝置分段百分比，
  例如「約 50%（分段回報）」；未取得數字時沿用 UPower 電量等級。
  此功能需要 0.2.0 以上原始碼或套件，沒有每 1% 更新的保證。
- 接收器存在不表示無線端一定開機；沒有資料時明確標示未知或未回報。
  藍牙僅顯示 BlueZ 確認已連線的裝置。
- 裝置別名只影響名稱與說明，不會讓未支援的鍵盤取得電量。
  自訂格式見 [使用說明](docs/USAGE.md)。
- APC UPS 資料來自已設定好的 apcupsd／apcaccess。看板僅唯讀監看，
  不安裝或修改寄信、關機門檻及 SMTP 憑證。
- Logitech G HUB 沒有官方 Linux 版。
  [Solaar](https://github.com/pwr-Solaar/Solaar) 是選用的 Logitech 管理工具，
  可搭配 [Piper](https://github.com/libratbag/piper) 管理滑鼠設定。
  兩者為選用套件；安裝 Solaar 後，看板會自動在背景呼叫其 CLI 查詢。
  安裝方式與限制見 [使用說明](docs/USAGE.md#logitech-電量與滑鼠設定)。

## 建置、驗證、更新與移除

    make test
    make deb

產物在 dist/。完整說明見 [BUILD.md](docs/BUILD.md)，
更新與移除見 [USAGE.md](docs/USAGE.md)。

## 隱私與授權

應用程式只讀取本機資料，沒有雲端上傳或遙測。
發布包不包含使用者設定、裝置清單、截圖、日誌或驗證憑證。
診斷預設不截圖；分享資料前請依 [PRIVACY.md](docs/PRIVACY.md) 檢查。
本專案使用 [MIT License](LICENSE)。
