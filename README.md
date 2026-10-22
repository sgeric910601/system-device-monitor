# 系統與裝置監看 · System Device Monitor

Ubuntu 桌面上的即時系統資源與周邊電量看板。提供彩色頂端列、完整視窗、
最近五分鐘曲線，以及目前連線中的 USB／藍牙設備。

A local GTK 3 system and peripheral battery monitor for Ubuntu.
No telemetry, accounts, or cloud service required.

## 功能

- 頂端列順序：耗電（黃）→ CPU（藍）→ CPU 溫度（橘）→ RAM（綠）→ GPU（紫）→ 網路（青）。
- 「電源」頁：整機耗電（APC 不斷電系統輸出，含螢幕等所有插在上面的設備）、3 分鐘平均、
  電力流向圖、耗電拆解與 7 天耗電歷史。
- 筆電（有內建電池時自動切換）：電量與狀態（放電中／充電中／接電・未充電／已充飽／已達上限）、
  剩餘時間或充滿時間、到 0% 與到 10% 的時間（系統預估與依近 3 分鐘平均兩種算法）、
  變壓器 → 系統 → 電池的電力流向、今日電量回顧、電量歷史，以及健康度、循環次數、
  充電上限等 18 項詳細資訊。頂端列「耗電」改顯示電量（例如「82%」）與系統的電池圖示。
- 示範模式 --preview 以記憶體中的示範資料開啟視窗（截圖用），自我檢查 --dump 印出
  回報問題時可以貼上的硬體摘要。
- 完整看板：系統負載、磁碟容量與讀寫速度、實體與虛擬網路、周邊電量、APC UPS。
  總覽頁的卡片點一下就開啟對應的詳細頁面。
- 「CPU／GPU」頁：使用者／系統比例、最近 2 分鐘曲線、各核心使用率、平均負載與時脈、
  每張顯示卡（NVIDIA 與 AMD）的使用率、顯示記憶體、溫度、風扇與耗電、
  CPU 使用率最高的 5 個 App（含圖示，同一個 App 的多個視窗與子程序合併），以及風扇與溫度分組。
- 「記憶體」頁：組成長條（App、共享、核心、快取、其他、空閒）與可用量、2 分鐘曲線、
  記憶體壓力（正常／偏高／嚴重，依核心的壓力統計與可用量變色）、Swap 與 zswap、
  使用記憶體最多的 5 個 App。
- 「磁碟」頁：每顆磁碟（含外接）的型號・連線方式・容量、健康度（NVMe 為 100%−已用壽命）、
  溫度、通電時間、出廠以來讀寫量、各分割區用量（超過 80% 橘色、90% 紅色）、
  讀寫速率與 2 分鐘曲線、開機以來的累計讀寫。
- 「網路」頁：主要介面（預設路由）的類型、名稱、IPv4、即時速率、2 分鐘曲線與開機以來的流量，
  以及其他連線中的介面。
- 溫度放在對應裝置名稱後；硬碟格式為「裝置 · 型號 · 容量 · 溫度」。
  依 sysfs 硬體路徑配對；沒有 hwmon 的硬碟改用磁碟服務回報的 SMART 溫度，
  兩者都沒有時標示「溫度未回報」。
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
    system-device-monitor --preview laptop-battery
    system-device-monitor --dump

--preview 可選 laptop-battery、laptop-charging、laptop-limit、desktop，
只顯示示範資料，不讀寫設定與歷史；--dump 的內容見 [使用說明](docs/USAGE.md#回報筆電問題--dump)。

CPU／記憶體／網速／磁碟讀寫每 2 秒更新；GPU／溫度／UPS 每 5 秒；
磁碟容量在總覽或磁碟頁開著時每 30 秒，其他時候每 5 分鐘。
各核心、App 排行、記憶體組成、磁碟 SMART 與 IPv4 只在對應頁面顯示時才讀取，
視窗關閉時不做這些額外的讀取。裝置事件觸發更新，另有每 5 秒重新確認。
Solaar 電量查詢每 60 秒執行一次，獨立背景工作不阻塞裝置清單。
網速摘要加總已連線實體介面，虛擬介面另外列出。

設定與日誌遵循 XDG 路徑，預設：
- 設定：~/.config/system-device-monitor/settings.json
- 選用裝置別名：~/.config/system-device-monitor/device-aliases.json
- 自啟：~/.config/autostart/system-device-monitor.desktop
- 日誌：~/.cache/system-device-monitor/app.log

## 硬體支援與限制

- 筆電電池：讀取 /sys/class/power_supply 中的系統電池（類型 Battery、不是滑鼠等周邊），
  名稱不限（BAT0、BAT1、CMB0、BATT 等），多顆電池合併計算；變壓器支援 Mains 與 USB-C PD。
  UPower 提供系統預估的剩餘與充滿時間，電源模式服務提供「低耗電模式」狀態。
  **充電上限目前只讀取顯示**，調整功能會在之後的版本加入。
- **筆電功能目前只以合成資料（模擬 ThinkPad、ASUS、Framework 等機型的 /sys 目錄）驗證，
  尚未在真正的筆電上測試。** 遇到問題時請附上 `system-device-monitor --dump` 的輸出。
- Linux 量不到變壓器的實際輸入瓦數，所以接電時整機耗電顯示「—」，不用猜測值代替。
  USB-C PD 回報的電壓與電流是與變壓器協商的契約（可用上限），只顯示為「協商 45 W」，
  不拿來推算整機耗電。
- CPU 型號／核心數自動讀取；CPU 溫度支援 k10temp 的 Tctl／Tdie，
  以及 coretemp 的 Package 溫度。其他感測器仍列在看板。
  「風扇與溫度」把感測器分成 CPU、顯示卡、儲存裝置、網路卡與其他，顯示平均與最高；
  感測器暫時讀不到時沿用 2 分鐘內的最近讀值。風扇轉速來自 hwmon，
  NVIDIA 顯示卡的風扇百分比來自 nvidia-smi；主機板沒有回報風扇時會註明。
- GPU 詳細使用率、VRAM、功耗與風扇百分比來自 nvidia-smi；AMD 顯示卡讀 sysfs。
  總覽卡片顯示第一張 NVIDIA（沒有時為 AMD 獨顯），「CPU／GPU」頁列出每一張。
- 各 App 的 CPU 與記憶體來自 systemd 為每個 App 建立的程序群組（cgroup v2），
  名稱與圖示取自 App 的桌面檔；GNOME Shell 與 Xorg 合併為「GNOME 桌面」，
  系統服務合併為一列。不讀任何程序的命令列。
- 硬碟健康度、溫度、通電時間與出廠以來讀寫量來自系統的磁碟服務（UDisks2），
  **不需要管理員權限**：NVMe 與 SATA 都支援；多數 USB 外接盒不轉送 SMART，
  會顯示「SMART 不支援」。SMART 屬性每分鐘、NVMe 壽命資料每 10 分鐘讀一次，
  只在總覽或磁碟頁開著時讀取。
- 「網路」頁的 IPv4 只顯示在視窗裡，不寫進日誌、模擬測試結果或 --dump。
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
