# 系統與裝置監看 · System Device Monitor

Ubuntu 桌面上的耗電、筆電電池、系統資源與周邊電量看板：彩色頂端列、完整視窗與桌面通知。
所有資料都在這台電腦上讀取，沒有帳號、雲端或遙測。

A local GTK 3 system and peripheral battery monitor for Ubuntu.
No telemetry, accounts, or cloud service; the only network access is an optional update check
against GitHub.

> **使用前請注意**
>
> - 筆電功能（電池、充電上限、智慧充電、精準喚醒）都只以合成的 /sys 資料、假的 logind 與
>   私人匯流排驗證，**尚未在真正的筆電上測試**。遇到問題請附上 `system-device-monitor --dump` 的輸出。
> - 一鍵更新從 0.3.0 開始：之後的新版可以在「設定 → 更新」直接安裝（.deb）。0.2.0 以前的版本
>   沒有這個功能，升級到 0.3.0 請依下方「更新」手動安裝一次。

## 功能

- 「電源」頁：桌機顯示整機耗電（APC 不斷電系統的輸出，含螢幕等所有插在上面的設備）、3 分鐘平均、
  電力流向、耗電拆解、7 天耗電歷史、電源模式與耗電排行；有內建電池時改為電池版面（電量、剩餘與
  充滿時間、今日電量回顧、18 項詳細資訊）。見[電源頁](docs/USAGE.md#電源頁與耗電歷史)與
  [筆電電池](docs/USAGE.md#筆電電池)。
- 補上歷史的空白：看板沒有記錄的時段（例如剛安裝之前、看板沒在執行的時候），由系統電源服務 UPower
  自己保存的電量紀錄補上最近 7 天內的部分：筆電補電池電量，桌機補不斷電系統電量（看板有在記錄時）。
  補上的部分在歷史圖上畫成較淡的虛線，圖下方標示「系統紀錄補上」；看板自己的紀錄絕不會被覆寫，
  今日回顧與拔電時間也只用看板自己的紀錄。這個功能只以私人匯流排上的假的 UPower 與一台桌機的
  不斷電系統（唯讀）驗證，尚未在真正的筆電上測試。
- 系統監控：總覽與「CPU／GPU」「記憶體」「磁碟」「網路」四頁，含用量最高的 5 個 App、磁碟健康度與
  溫度、主要網路介面；只在頁面顯示時取樣。見[系統監控頁](docs/USAGE.md#系統監控頁cpugpu記憶體磁碟網路)。
- 頂端列：分開顯示或合併成一個圖示，耗電項目有五種格式與依電量畫的電池圖示；選單可以切換電源模式、
  查看每台設備的電量。見[頂端列](docs/USAGE.md#頂端列)。
- 周邊設備：目前連線的 USB 與藍牙設備及電量；Logitech G304／G305 可透過選用的 Solaar 讀取分段電量。
  見 [Logitech 電量](docs/USAGE.md#logitech-電量與滑鼠設定)。
- 通知：筆電低電量與充電完成、周邊設備低電量、不斷電系統停電與電量低。見[通知](docs/USAGE.md#通知)。
- 筆電的[充電上限](docs/USAGE.md#充電上限)與[智慧充電](docs/USAGE.md#智慧充電)（「幾點前充到幾 %」），
  以及桌機與筆電都能用的 CPU 功耗取樣：都是選用的管理員功能，見下方「選用的管理員元件」。
- [充電夥伴](docs/USAGE.md#充電夥伴)、[桌面寵物](docs/USAGE.md#桌面寵物)與四種
  [程式圖示顏色](docs/USAGE.md#程式圖示顏色)。
- 設定視窗的所有變更立即套用：更新頻率、省電模式、監控模組、顯示項目等，見
  [視窗與設定](docs/USAGE.md#視窗與設定)。

## 系統需求

- Ubuntu 24.04、GNOME 46，使用系統的 Python 3.12（/usr/bin/python3；不要用 Conda 等其他 Python）。
- GNOME 的 AppIndicator 擴充功能（套件 gnome-shell-extension-appindicator，Ubuntu 預設已啟用）：
  沒有它就看不到頂端列。
- 必要的 apt 套件（.deb 會自動安裝；原始碼安裝由 ./install-dependencies.sh 安裝）：
  python3、python3-gi、python3-gi-cairo、gir1.2-gtk-3.0、gir1.2-gudev-1.0、
  gir1.2-ayatanaappindicator3-0.1、gir1.2-notify-0.7（桌面通知）、librsvg2-common、util-linux，
  以及 .deb 另外需要的 openssl（驗證一鍵更新的簽章）。
- 選用（Ubuntu 桌面版通常已經安裝前四個）：upower 與 power-profiles-daemon（筆電的系統預估與
  電源模式）、bluez（藍牙設備）、udisks2（磁碟健康度）、apcupsd（APC 不斷電系統）、NVIDIA 驅動附的
  nvidia-smi、solaar（Logitech 分段電量），以及管理員元件用來詢問密碼的 pkexec。
- 桌面寵物需要 X11 工作階段（「Ubuntu on Xorg」）；其他功能在 Wayland 也能使用。

## 安裝

兩種方式擇一，不要同時安裝：兩者共用設定，但啟動器與管理員元件會互相衝突。

### .deb 套件（建議）

從 [Releases](https://github.com/sgeric910601/system-device-monitor/releases) 下載 .deb 與
SHA256SUMS，在下載目錄執行：

    sha256sum --ignore-missing -c SHA256SUMS
    sudo apt install ./system-device-monitor_0.3.1_all.deb

在應用程式選單搜尋「系統與裝置監看」，或執行 system-device-monitor。套件已經包含管理員元件，
但安裝時不啟用任何服務，也不寫入任何人的家目錄。

### 原始碼資料夾

    git clone https://github.com/sgeric910601/system-device-monitor.git
    cd system-device-monitor
    ./install-dependencies.sh
    /usr/bin/python3 install.py
    ~/.local/bin/system-device-monitor

只有 install-dependencies.sh 需要 sudo（安裝 apt 套件）；install.py 與看板都以一般使用者執行，
install.py 寫入 ~/.local/bin 的啟動器、應用程式選單的項目與登入自啟。原始碼資料夾可以放在任何地方
（登入時最多等它所在的磁碟 60 秒），搬移之後重新執行 install.py。要使用管理員功能時，另外安裝
管理員元件（見下一節）。

### 在兩種方式之間切換

設定、歷史與日誌放在家目錄的 XDG 資料夾，兩種方式共用，切換不會遺失。先從頂端列選單「退出」看板。

- 原始碼 → .deb：在原本的原始碼資料夾執行（第一行只有安裝過管理員元件時才需要），再安裝套件：

      /usr/bin/python3 -B install.py --system-components remove
      /usr/bin/python3 install.py --uninstall
      sudo apt install ./system-device-monitor_0.3.1_all.deb

  順序不能顛倒：套件已安裝時，原始碼版的 --system-components 會拒絕執行。--uninstall 會刪除
  登入自啟，換好之後在「設定 → 一般」重新開啟。
- .deb → 原始碼：先移除套件，再依上面「原始碼資料夾」的步驟安裝（原本開著的登入自啟會改指向
  新的啟動器）：

      sudo apt remove system-device-monitor

## 選用的管理員元件

需要管理員權限的功能都是選用的。看板本身一律以一般使用者執行，開啟程式時也不會要求密碼；
只有你在設定或「電源」頁開啟這些功能時，才會跳出管理員密碼視窗。

| 元件 | 做什麼 | 什麼時候要輸入管理員密碼 |
|---|---|---|
| CPU 功耗取樣（sdm-cpu-power 與取樣服務） | 讀取處理器的 RAPL 耗電計數器（只有 root 能讀），在「電源」頁多出 CPU 瓦數 | 在「設定 → 管理員功能」開啟或關閉時，每次都要 |
| 充電上限（sdm-charge-limit，筆電） | 把電池的充電上限設為 80–100%，可選「開機與喚醒後保留」 | 智慧充電自動調整上限：沒有授權時要密碼（約 5 分鐘內不必重輸）；允許無人時自動調整之後免密碼。手動拖動滑桿與切換保留一律要密碼（約 5 分鐘內不必重輸） |
| 精準喚醒（sdm-wake，筆電） | 智慧充電在該開始充電前以 RTC 鬧鐘叫醒電腦 | 設定、取消喚醒：沒有授權時要密碼（約 5 分鐘內不必重輸）；允許無人時自動調整之後免密碼（看板只在允許之後才使用精準喚醒） |
| 允許無人時自動調整（sdm-unattended，筆電） | 寫入只給你的 polkit 規則：只在你登入並使用這台電腦時，智慧充電自動調整的充電上限與喚醒時間免密碼 | 開啟與關閉時，每次都要 |
| 一鍵更新（sdm-update，只有 .deb） | 以 apt-get 安裝驗證過簽章的新版本 | 每次安裝；這個版本還不能使用（見上方） |

- .deb：已經包含全部元件，安裝時不啟用任何東西。`sudo apt remove system-device-monitor` 一併移除，
  同時撤銷無人授權的規則、取消排定的喚醒並刪除保留的充電上限。
- 原始碼資料夾：在資料夾裡執行下列指令安裝或移除前四種元件（一鍵更新只隨 .deb 提供）。會跳出
  管理員密碼視窗（或在終端機詢問）一次；只安裝已經提交的版本，已經安裝 .deb 時會拒絕。

      /usr/bin/python3 -B install.py --system-components install
      /usr/bin/python3 -B install.py --system-components remove

  安裝之後才在設定裡開啟各項功能；remove 先撤銷無人授權的規則、取消排定的喚醒，再刪除所有元件。

各元件的用法與狀態見[使用說明](docs/USAGE.md#管理員元件)，威脅模型見 [SECURITY.md](docs/SECURITY.md)。

## 使用

從應用程式選單啟動。關閉視窗後看板繼續在頂端列執行，頂端列選單的「退出」才結束。點頂端列的項目
打開選單：每個項目一行（點一下開啟它的頁面）、設備電量、電源模式、開啟看板、設定…與退出。
第一次啟動時會詢問監控範圍，並開啟登入自啟（可以在「設定 → 一般」關閉）。

| 指令 | 用途 |
|---|---|
| system-device-monitor | 開啟看板；已經在執行時開啟同一個視窗 |
| system-device-monitor --background | 只在頂端列執行、不開視窗（登入自啟使用） |
| system-device-monitor --version | 印出版本 |
| system-device-monitor --preview laptop-battery | 示範模式：只用記憶體中的示範資料開啟視窗（截圖用），另有 laptop-charging、laptop-limit、desktop |
| system-device-monitor --dump | 印出回報問題時可以貼上的硬體摘要（JSON，不含序號、位址或路徑） |
| system-device-monitor --charging-plan-status | 以 JSON 唯讀印出智慧充電的排程、階段與最後 20 行紀錄 |
| system-device-monitor --test-notification | 請正在執行的看板送出一則測試通知 |

原始碼安裝的指令在 ~/.local/bin/system-device-monitor。每個選項的細節見
[命令列選項](docs/USAGE.md#命令列選項)，各頁的內容、取樣頻率與疑難排解見[使用說明](docs/USAGE.md)。

## 更新

- 「設定 → 更新」每 24 小時最多向 GitHub 確認一次有沒有新的正式版，有就通知一次（可以關閉）；
  「立即檢查」也列出測試版。
- 一鍵更新（.deb）：有新版本時按「安裝更新」，看板先以內建的維護者公鑰驗證 Ed25519 簽章與檢查碼，
  再要求管理員密碼安裝，完成後自動重新啟動。
- 手動更新（0.2.0 以前的版本升級到 0.3.0 也用這個方式）：
  - .deb：從 Releases 下載新的 .deb 與 SHA256SUMS，確認檢查碼後以 sudo apt install ./新的檔名.deb
    安裝，再從頂端列退出並重新開啟看板。
  - 原始碼資料夾：退出看板後執行 git pull --ff-only 與 /usr/bin/python3 install.py；「設定 → 管理員功能」
    顯示需要重新安裝時，再執行一次 install.py --system-components install。

設定、歷史與登入自啟的選擇都會保留。詳見[使用說明](docs/USAGE.md#更新)。

## 移除

.deb 安裝先在「設定 → 一般」關閉登入自啟（apt 不會刪除家目錄裡的自啟檔）；兩種方式都先從頂端列選單
「退出」看板，再執行：

- .deb：

      sudo apt remove system-device-monitor
      sudo apt purge system-device-monitor

  remove 移除程式與所有管理員元件，並撤銷無人授權的規則、取消排定的喚醒、刪除保留的充電上限；
  purge 另外刪除一鍵更新的執行鎖，並再確認一次這些系統檔案都已刪除。
- 原始碼資料夾（第一行只有安裝過管理員元件時才需要）：

      /usr/bin/python3 -B install.py --system-components remove
      /usr/bin/python3 install.py --uninstall

  --uninstall 刪除啟動器、應用程式選單的項目、登入自啟與本程式寫入的程式圖示；原始碼資料夾本身
  留著，可以自己刪除。

兩種方式都保留家目錄裡的設定、歷史與日誌；要完全清除時，刪除[檔案位置](docs/USAGE.md#檔案位置)
列出的 system-device-monitor 資料夾。

## 刻意不做的功能

macOS 版有、但這個版本刻意不做的功能：

- 分開設定滑鼠與觸控板的捲動方向：GNOME 的「設定 → 滑鼠與觸控板」已經分開提供。
- DMG 安裝檔與程式碼簽章：那是 macOS 的發布方式；這裡以 .deb 套件發布，一鍵更新另外以
  Ed25519 簽章驗證。
- 每個 App 的 GPU 耗電：要不斷掃描每個程序的顯示卡用量，負擔太大；耗電排行依 CPU 佔比估算。
- 左右耳機與充電盒分開顯示電量：Linux 的藍牙（BlueZ）每台設備只回報一個電量。

## 硬體支援與限制

- 整機耗電來自已經設定好的 APC 不斷電系統（apcupsd／apcaccess，只讀取、不修改它的設定）；
  沒有不斷電系統時只列出顯示卡等已知元件的合計。
- 筆電電池：/sys/class/power_supply 中的系統電池（名稱不限，多顆合併計算）；充電上限需要電池提供
  charge_control_end_threshold。Linux 量不到變壓器的實際輸入瓦數，所以接電時整機耗電顯示「—」。
- 顯示卡：NVIDIA 來自 nvidia-smi，AMD 來自 sysfs。磁碟健康度來自系統的磁碟服務（UDisks2），不需要
  管理員權限；多數 USB 外接盒不轉送 SMART。
- 每個 App 的用量來自 systemd 的程序群組（cgroup v2），不讀任何程序的命令列。
- 周邊電量來自 UPower 與 BlueZ；只回報等級的設備顯示等級，不換成虛構的百分比。
- 桌面寵物需要 X11；Wayland 不允許程式決定視窗的位置與層級，寵物會改成一般的小視窗。

各資料來源的細節見[使用說明](docs/USAGE.md#資料來源與限制)。

## 文件

- [使用說明](docs/USAGE.md)：各頁、設定、頂端列、通知、管理員元件、更新、移除、檔案位置與疑難排解。
- [隱私說明](docs/PRIVACY.md)：讀取與保存哪些資料，以及唯一的網路存取。
- [安全說明](docs/SECURITY.md)：管理員元件、無人授權與一鍵更新的威脅模型。
- [建置與開發](docs/BUILD.md)：測試、打包、發布與程式架構。
- [更新紀錄](CHANGELOG.md)。

## 隱私與授權

看板只讀取本機資料，沒有雲端上傳或遙測；唯一的網路存取是選用的更新檢查（只連到 GitHub，可以在
「設定 → 更新」關閉）。發布包不含使用者設定、裝置清單、截圖、日誌或憑證；分享診斷資料前請依
[隱私說明](docs/PRIVACY.md)檢查。本專案使用 [MIT License](LICENSE)。
