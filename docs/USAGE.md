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
- UPS 未回報：確認原有 apcupsd 已設定並可執行 apcaccess status。
- SATA／USB 硬碟溫度未回報：目前只讀 hwmon，不會用其他硬碟溫度代替。
- 電量只有等級：這是目前資料來源的精度，並不是宣稱裝置沒有電池。
- 藍牙或 UPower 暫時無法取得：USB 與系統監看仍繼續，下一次更新會重試。

## 本機診斷

先退出正在執行的實例，以免單一實例機制把請求交給舊程序：

    system-device-monitor --smoke-test --output-dir /tmp/monitor-check

約 18 秒後結束。只產生簡短的 smoke.json，不列硬體、主機名稱或裝置識別碼。
應用程式必須在有顯示器及 D-Bus 的桌面工作階段執行。
如需自行檢視畫面，可額外加 --capture，只截取應用程式視窗，
不截整個桌面。截圖可能含私人裝置名稱或掛載路徑，勿直接上傳。
