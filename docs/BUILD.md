# 建置與開發

## 環境

Ubuntu 24.04，使用系統 Python。此專案沒有 pip 執行期依賴。

    ./install-dependencies.sh
    sudo apt install make dpkg-dev gitleaks desktop-file-utils
    make test
    make privacy
    make deb

dist/system-device-monitor_0.2.0_all.deb 為純 Python／SVG，
Architecture: all。建置不需要 sudo；版本由 VERSION 讀取。

套件安裝：
- /usr/bin/system-device-monitor：啟動器。
- /usr/share/system-device-monitor：執行期程式與圖示。
- /usr/share/applications/system-device-monitor.desktop：桌面入口。
- /usr/share/doc/system-device-monitor：文件與授權。

package-files.json 明確列出公開原始碼及執行期檔案。
建置只複製列出的檔案，拒絕符號連結與逃出專案的路徑，
不會打包整個工作目錄。

## 測試

    make test
    make privacy

單元測試涵蓋計數器重設、CPU guest 計算、電量等級、服務失敗、
裝置去重、拔除、溫度配對、設定保留及安裝路徑。
另涵蓋 Solaar 分段電量解析、來源合併、逾時、離線／重新連線與快取過期。
測試資料為人工建立；不提交實機擷取資料。

GUI smoke test 需要桌面或 Xvfb、system bus 與 session bus。
CI 使用 Ubuntu 24.04，建置並安裝 .deb 後在 Xvfb 下執行。

## 發布

先更新 VERSION 與 CHANGELOG.md，執行測試及隱私檢查，
只提交 package-files.json 中的來源檔案。

    make test
    make privacy
    make deb

提交後產生與該提交一致的來源壓縮包和檢查碼：

    make release-assets

在 push 之前掃描完整 Git 歷史，並解包檢查原始碼壓縮檔和 .deb。
不要只依賴 .gitignore；已經被追蹤的敏感檔不會自動被忽略。
dist/ 的 .deb、.tar.gz 和 SHA256SUMS 放在 GitHub Release，不提交到 Git。
來源壓縮包由 Git HEAD 產生，不能包含尚未提交的變更。

## 架構

- device_monitor.py：GTK 視窗、彩色指示器、圖表及背景排程。
- collectors.py：/proc、/sys、BlueZ、UPower、NVIDIA、APC 唯讀資料來源。
- model.py：計數器與電量格式化；runtime.py：XDG 路徑與安裝共用功能。
- logitech_battery.py：選用 Solaar CLI、G304／G305 電量解析及短期記憶體快取。

Solaar 為外部選用工具，沒有複製或封裝其原始碼；Piper 不參與看板執行。
可使用 ./install-dependencies.sh --logitech 安裝測試用工具。

慢速外部命令有逾時；不同資料來源獨立退回未知狀態，
不應把單一硬體失敗擴大為整個看板失效。
