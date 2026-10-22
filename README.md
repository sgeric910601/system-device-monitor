# 系統與裝置監看 · System Device Monitor

Ubuntu 桌面上的即時系統資源與周邊電量看板。提供彩色頂端列、完整視窗、
最近五分鐘曲線，以及目前連線中的 USB／藍牙設備。

A local GTK 3 system and peripheral battery monitor for Ubuntu.
No telemetry, accounts, or cloud service required.

## 功能

- 視窗與設定：主視窗左側是頁面側欄（電源、系統總覽、CPU／GPU、記憶體、磁碟、網路、連線設備），
  視窗寬度小於 640 px 時側欄收起，改用標題列左上角的頁面選單；關閉視窗時記住目前的頁面。
  標題列的齒輪按鈕開啟設定視窗（一般、頂端列、監控模組、顯示項目、關於），所有變更立即套用，
  不需要重新啟動。第一次啟動時詢問監控範圍：「只看電源」或「電源＋系統監控」
  （桌機為「只看耗電」／「耗電＋系統監控」）。視窗寬度 480 px 起都能正常顯示。
- 頂端列兩種模式：「分開顯示」時耗電（筆電為電池）、CPU、CPU 溫度、RAM、GPU、網路各一個圖示
  （依此順序，可個別關閉）；「合併成一個」時只留一個圖示，開啟的項目串成一行，例如
  「297 W｜CPU 12%｜54°C｜RAM 34%｜GPU 30%｜↓340K ↑12K」，筆電為「82%（3:25）｜CPU 12%…」。
  選單列出每個項目（點一下開啟它的頁面）、設備電量、電源模式、開啟看板、設定…與退出。
- 耗電項目有五種顯示格式（圖示＋百分比、只有圖示、只有百分比、圖示＋剩餘時間、
  圖示內百分比＋剩餘時間）。筆電的電池圖示依電量填滿：正在放電且電量低於低電量門檻（預設 20%）
  時變紅、接電時為綠色，充電中加閃電、接電未充電加插頭；桌機為插頭圖示，
  不斷電系統改用電池時改成橘色的不斷電系統圖示。
- 更新頻率：每秒、每 2 秒（預設）、每 5 秒、每 10 秒。溫度、不斷電系統與電池最快每 5 秒，
  nvidia-smi 最快每 2 秒，頁面專用的資料也不會比設定的頻率快。
- 省電模式：更新頻率至少每 2 秒、電力流向圖不播放動畫；可設定「只在使用電池時
  （筆電電池或不斷電系統改用電池）啟用」。
- 電源模式：省電／平衡／效能（系統的電源模式服務 power-profiles-daemon），可從頂端列選單或
  「電源」頁切換，與 GNOME 快速設定相同，不需要輸入密碼。
- 監控模組：系統監控總開關與 CPU／GPU、記憶體、磁碟、網路四個模組；關閉的模組立即停止取樣，
  並隱藏它的頁面、總覽卡片與頂端列項目。
- 顯示項目：可隱藏電力流向細項（顯示卡、處理器、其他；隱藏顯示卡時 NVIDIA 與 AMD 一起隱藏）
  與詳細資訊欄位。
- 「電源」頁：整機耗電（APC 不斷電系統輸出，含螢幕等所有插在上面的設備）、3 分鐘平均、
  電力流向圖、耗電拆解、7 天耗電歷史、電源模式與耗電排行（CPU 用量最高的 App；
  目前無法取得 CPU 功耗，依 CPU 使用率排序）。流向圖在看得到時依瓦數流動（最多每秒 15 格），
  最小化、省電模式或看不到時改為靜態箭頭。
- 筆電（有內建電池時自動切換）：電量與狀態（放電中／充電中／接電・未充電／已充飽／已達上限）、
  剩餘時間或充滿時間、到 0% 與到 10% 的時間（系統預估與依近 3 分鐘平均兩種算法）、
  變壓器 → 系統 → 電池的電力流向、今日電量回顧、電量歷史，以及健康度、循環次數、
  充電上限、電源模式等 18 項詳細資訊。頂端列「耗電」改顯示電量（例如「82%」）與電池圖示。
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
- 首次啟動啟用登入自啟，可在「設定 → 一般」關閉；更新保留既有選擇。

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

點擊任一頂端列項目會打開選單：每個項目一行（點一下開啟對應頁面）、設備電量、電源模式、
開啟看板、設定…與退出。設定視窗可選擇頂端列模式與項目、顯示格式、更新頻率、省電模式、
監控模組、顯示項目與登入自啟；UPS 詳細資料在看板中，不額外建立 UPS 頂端列圖示。

    system-device-monitor --background
    system-device-monitor --version
    system-device-monitor --preview laptop-battery
    system-device-monitor --dump

--preview 可選 laptop-battery、laptop-charging、laptop-limit、desktop，
只顯示示範資料，不讀寫設定與歷史；--dump 的內容見 [使用說明](docs/USAGE.md#回報筆電問題--dump)。

CPU／記憶體／網速／磁碟讀寫依「設定 → 一般 → 更新頻率」（預設每 2 秒）；
GPU 讀值來自常駐的 nvidia-smi（跟著更新頻率，最快每 2 秒）；溫度／UPS／電池最快每 5 秒；
磁碟容量在總覽或磁碟頁開著時每 30 秒，其他時候每 5 分鐘。
各核心、App 排行、記憶體組成、磁碟 SMART 與 IPv4 只在對應頁面顯示時才讀取，
視窗關閉時不做這些額外的讀取。裝置事件觸發更新，另有每 5 秒重新確認。
Solaar 電量查詢每 60 秒執行一次，獨立背景工作不阻塞裝置清單。
網速摘要加總已連線實體介面，虛擬介面另外列出。

設定與日誌遵循 XDG 路徑，預設：
- 設定：~/.config/system-device-monitor/settings.json（舊版五個頂端列項目）
  與同目錄的 preferences.json（其他設定）
- 視窗狀態（最後一頁）：~/.local/state/system-device-monitor/state.json
- 頂端列耗電圖示：$XDG_RUNTIME_DIR/system-device-monitor/icons/（登出就清除）
- 選用裝置別名：~/.config/system-device-monitor/device-aliases.json
- 自啟：~/.config/autostart/system-device-monitor.desktop
- 日誌：~/.cache/system-device-monitor/app.log

## 硬體支援與限制

- 筆電電池：讀取 /sys/class/power_supply 中的系統電池（類型 Battery、不是滑鼠等周邊），
  名稱不限（BAT0、BAT1、CMB0、BATT 等），多顆電池合併計算；變壓器支援 Mains 與 USB-C PD。
  UPower 提供系統預估的剩餘與充滿時間，電源模式服務提供目前的電源模式（省電／平衡／效能）。
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
