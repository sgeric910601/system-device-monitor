# 使用、更新與移除

## 桌面與背景執行

從應用程式選單啟動看板。關閉視窗會隱藏到頂端列，
再次啟動會開啟同一個視窗，不產生第二個監看實例。
頂端列的「退出」會結束程序。

原始碼安裝可明確控制登入啟動：

    /usr/bin/python3 install.py --autostart on
    /usr/bin/python3 install.py --autostart off

預設 preserve：首次安裝啟用，以後保留使用者選擇。
.deb 不在安裝過程修改任何人的家目錄；第一次啟動時才建立該使用者設定。
桌面啟動項目使用絕對路徑，不依賴互動式 shell 的 PATH 或 Conda。

## 電源頁與耗電歷史

「電源」頁的整機耗電 = 不斷電系統負載百分比 × 額定功率，是不斷電系統的總輸出，
包含螢幕、喇叭等所有插在上面的設備，不只是電腦本身。負載以百分比回報，
所以數字會以約額定功率 1% 為一格跳動。

- 拆解：已知元件（目前為 NVIDIA 顯示卡與 AMD 獨立顯示卡）各自列出，
  「其他」＝整機減去已知元件，含螢幕與電源損耗。CPU 功耗需要管理員權限，之後的版本才提供。
- AMD 內建顯示（處理器內的顯示晶片）的功耗讀值代表整顆處理器，單位尚未校正，暫不顯示。
- 沒有不斷電系統時，只能顯示已知元件合計，並註明無法取得整機耗電。
- 改由不斷電系統電池供電時，標題改成橘色並顯示剩餘分鐘數；電池電量低時改為紅色。
- 3 分鐘平均為指數移動平均（時間常數 180 秒）。

耗電歷史每分鐘記錄一筆，保留 7 天，存在 XDG 資料目錄
（預設 ~/.local/share/system-device-monitor/history.json，資料夾權限 0700）。
每累積 15 筆與退出時寫入。檔案無法解讀時，先原樣另存為
history-<時間>.json.bak（不覆寫既有備份）再從空白開始；
無法讀取或無法備份時，該次執行不寫入歷史，原檔保持不變。

頂端列「耗電」指示器可在「設定 → 頂端列顯示 → 整機耗電」關閉；
這個選項存在 preferences.json，不影響舊版的 settings.json。

## Logitech 電量與滑鼠設定

Ubuntu 沒有官方 G HUB；以下是選用的 Linux 工具：

    sudo apt install solaar piper ratbagd

原始碼依賴腳本也可使用 ./install-dependencies.sh --logitech 一併安裝。
安裝後重新插拔 Logitech 接收器，讓套件附帶的 udev 權限規則生效；
看板和 Solaar 都以一般使用者執行。

- Solaar：從應用程式選單開啟，查看裝置電量與支援的設定。
- Piper：從應用程式選單開啟，查看 DPI、按鍵及設定檔。可用功能依型號而異。
- 看板：重新啟動後自動偵測 Solaar，無須讓 Solaar 圖形介面常駐。
  套件可能加入 Solaar 登入啟動項目；若只使用看板電量，可在啟動應用程式中停用它。

目前驗證的電量協定為 G304／G305（HID++ 配對裝置 ID 4074）的 BATTERY STATUS。
看板每 60 秒查詢一次，單次最多等待 15 秒，顯示資料來源、更新時間及下一級電量。
「約 50%（分段回報）」代表裝置給出的級距，不是連續精確電量。
只有等級時仍保留等級；電池狀態協定的 0／未知不會被誤顯示為 0%。

讀不到、逾時、移除或核心回報離線時，停止使用先前的數字；
快取最長 90 秒。裝置狀態通常在下一次 5 秒更新時反映，
重新連線後等下一次 Solaar 查詢才恢復數字。

Piper 可能辨識滑鼠，卻對作用中的設定檔或 DPI／回報率呈現不一致讀值。
遇到此情況，先核對 Solaar 與裝置目前行為，再使用 Piper 的 Apply；
看板只查電量，不會修改 DPI、按鍵、配對或韌體。

自行診斷可執行 solaar show。其完整輸出可能含裝置序號、配對識別碼及設定，
請只分享去識別化的電量摘要，不要直接上傳原始輸出。

## 裝置名稱覆寫

在 XDG 設定目錄建立 device-aliases.json。鍵值為
「USB vendor:product|manufacturer|product name」，必須完全符合系統描述。
以下全為虛構範例，請使用本機描述替換：

    {
      "1234:5678|Example Vendor|Example Receiver": {
        "name": "我的無線鍵盤接收器",
        "status": "接收器已連接 · 鍵盤無線狀態未知",
        "battery": "有電池 · 電量未回報"
      }
    }

重啟應用程式後生效。此檔案只留在本機，不應提交到 GitHub。
缺少或格式錯誤的覆寫會退回裝置原始名稱。

## 更新

原始碼版本，先從頂端列退出，再執行：

    git pull --ff-only
    /usr/bin/python3 install.py
    ~/.local/bin/system-device-monitor

.deb 版本下載新版本，以 sudo apt install ./新版本.deb 更新後重啟應用程式。
兩種方式都保留顯示項目及自動啟動設定。

## 移除

先使用頂端列「退出」，並在設定關閉登入自啟。

原始碼安裝：

    /usr/bin/python3 install.py --uninstall

.deb 安裝：

    sudo apt remove system-device-monitor

移除保留使用者設定及日誌，可自行刪除各 XDG 目錄下的 system-device-monitor
子目錄。apt 不會代替使用者刪除家目錄內容。

## 疑難排解

- 沒有頂端列：確認 GNOME AppIndicator 擴充功能已啟用；必要時登出再登入。
- 找不到 gi 或 Cairo：使用 /usr/bin/python3，並重跑相依套件安裝腳本。
- GPU 未回報：確認 NVIDIA 驅動與 nvidia-smi 可用；不影響其他功能。
  看板執行期間會常駐一個 nvidia-smi 程序（每 5 秒輸出一次），結束時一併停止；
  它意外結束時會在 1 秒到 60 秒之間逐次加倍等待後重新啟動。
- 耗電顯示「無法取得整機耗電」：表示讀不到不斷電系統，請確認 apcaccess status 可用。
- UPS 未回報：確認原有 apcupsd 已設定並可執行 apcaccess status。
- SATA／USB 硬碟溫度未回報：目前只讀 hwmon，不會用其他硬碟溫度代替。
- 電量只有等級：這是目前資料來源的精度，並不是宣稱裝置沒有電池。
- 藍牙或 UPower 暫時無法取得：USB 與系統監看仍繼續，下一次更新會重試。

## 本機診斷

先退出正在執行的實例，以免單一實例機制把請求交給舊程序：

    system-device-monitor --smoke-test --output-dir /tmp/monitor-check

約 20 秒後結束。只產生簡短的 smoke.json（含依序顯示過的頁面名稱），
不列硬體、主機名稱或裝置識別碼。耗電歷史只存在記憶體，不讀也不寫 history.json。
應用程式必須在有顯示器及 D-Bus 的桌面工作階段執行。
如需自行檢視畫面，可額外加 --capture，只截取應用程式視窗，
不截整個桌面。截圖可能含私人裝置名稱或掛載路徑，勿直接上傳。
