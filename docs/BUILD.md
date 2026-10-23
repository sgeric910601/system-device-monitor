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
相依套件含 gir1.2-notify-0.7（桌面通知函式庫），供之後的通知功能使用；
install-dependencies.sh 與 CI 也會安裝它。

套件安裝：
- /usr/bin/system-device-monitor：啟動器。
- /usr/share/system-device-monitor：執行期程式（device_monitor.py 與 sdm/ 套件）與圖示。
- /usr/share/applications/system-device-monitor.desktop：桌面入口。
- /usr/share/doc/system-device-monitor：文件與授權。
- /usr/libexec/system-device-monitor：管理員小程式 sdm-cpu-power 與 sdm-cpu-power-service（0755）。
- /usr/share/polkit-1/actions/tw.local.system-device-monitor.policy：polkit 動作（0644）。
- /usr/lib/systemd/system/system-device-monitor-cpu-power.socket 與 .service：
  CPU 功耗取樣的 socket 與服務（0644）。安裝後預設不啟用。

package-files.json（格式 2）明確列出：
- runtime：複製到 /usr/share/system-device-monitor 下相同相對路徑的執行期檔案，權限 0644。
- system：系統檔案，各自指定權限（不得超過 0755）；目前為上述管理員小程式、
  polkit 動作與兩個 systemd 單元。
  只能安裝到 /usr/libexec/system-device-monitor、/usr/share/system-device-monitor、
  /usr/share/polkit-1/actions、/usr/lib/systemd/system、/usr/share/applications、
  /usr/share/icons/hicolor 之下（清單在 sdm/constants.py）；套件不安裝任何 /etc 檔案。
- maintainer：preinst、postinst、prerm、postrm 安裝前後腳本，權限 0755；
  目前為 packaging/debian/ 的 postinst、prerm、postrm（POSIX sh、set -e）：
  postinst 只重新載入 systemd，socket 原本已啟用時才重新啟動它，從不自動啟用；
  prerm 在移除時停用並停止 socket 與服務、刪除 drop-in；postrm 在 purge 時刪除 drop-in
  （套件不建立其他狀態目錄）。DPKG_ROOT 不是空的時不碰執行中的 systemd。
- source：公開原始碼清單，必須涵蓋所有 git 追蹤檔案。

建置只複製列出的檔案，拒絕符號連結、未正規化或逃出專案的路徑，
以及重複或互相包含的安裝位置，不會打包整個工作目錄。
目錄一律 0755，檔案時間固定，以利重現建置。

## 測試

    make test
    make import-check
    make privacy

make test 執行 /usr/bin/python3 -B -m unittest discover -s tests -t . -v。
測試放在 tests/，必須有 tests/__init__.py，否則探索會略過整個目錄。

單元測試涵蓋計數器重設、CPU guest 計算、電量等級、服務失敗、
裝置去重、拔除、溫度配對、設定保留及安裝路徑。
另涵蓋 Solaar 分段電量解析、來源合併、逾時、離線／重新連線與快取過期；
設定相容（舊 settings.json 格式、preferences.json 驗證與原子寫入）；
打包清單一致性、檔案權限與安裝位置限制；匯入檢查與 CPU 量測解析；
不斷電系統解析、nvidia-smi 串流（以暫存目錄中的假執行檔測試重啟與結束）、
AMD 假 /sys、電力拆解、3 分鐘平均、歷史檔與圖表分段、頁面文字與幾何。
筆電部分以 tests/fixtures/sysfs.py 產生的假 /sys（ThinkPad、ASUS、Framework、無門檻、
到上限未充電、狀態未知、兩顆電池、周邊電池、壞值）測試讀取，並移植參考專案的
充電狀態濾波、剩餘時間、電源情境與今日回顧數值；另有 --dump 隱私檢查，
以及在 Xvfb 下實際執行示範模式、確認不寫入任何 XDG 目錄的整合測試（需要 xvfb-run）。
測試資料為人工建立；不提交實機擷取資料。

make import-check 從指定根目錄匯入 sdm/ 底下每個模組。
CI 也對解包後的 .deb 執行同一檢查，確認套件內容完整：

    /usr/bin/python3 -B scripts/import_check.py --root 解包目錄/usr/share/system-device-monitor

管理員元件（helper/）以 root 執行，測試一律不需要 root：開關小程式與原始碼安裝腳本以
假根目錄（擁有者＝執行測試的使用者）、假的 systemctl 與 dpkg-query 測試參數表、權限、
O_NOFOLLOW 與信任檢查；取樣服務以暫存檔代替 energy_uj、暫存目錄的 Unix socket 代替
systemd 交來的 socket 與 OpenFile= 描述子，以文字代替 /proc/self/status 測試權限斷言，
並以假時鐘測試全部連線共用的 1 秒視窗與連線速率限制。單元檔測試確認每個強化設定、
.deb 與原始碼安裝版只差名稱與路徑；polkit 測試確認 exec.path 與預設授權；
安裝前後腳本以 DPKG_ROOT 指向暫存目錄實際執行。

單元檔可以在解包後的 .deb 上驗證（只讀，不需要 root）：把主機的 target 單元複製到解包目錄的
usr/lib/systemd/system 後執行

    systemd-analyze --root=解包目錄 verify 解包目錄/usr/lib/systemd/system/system-device-monitor-cpu-power.socket 解包目錄/usr/lib/systemd/system/system-device-monitor-cpu-power.service
    systemd-analyze security --offline=true --root=解包目錄 解包目錄/usr/lib/systemd/system/system-device-monitor-cpu-power.service

CI 安裝 .deb 後另外檢查權限、pkaction 的預設授權、以 sudo 執行小程式的錯誤參數、
開啟後服務確實以臨時帳號 sdm-cpu-power 執行、所有能力欄位都是 0，以及移除與清除後
沒有留下任何單元或 drop-in。

GUI smoke test 需要桌面或 Xvfb、system bus 與 session bus。
CI 使用 Ubuntu 24.04，建置並安裝 .deb 後在 Xvfb 下執行。

## CPU 用量量測

    /usr/bin/python3 -B scripts/measure_cpu.py 程序代號 600

讀取 /proc/程序代號/stat 在量測前後的 CPU 時間（含已回收的子程序，
例如 nvidia-smi、apcaccess），輸出單一核心百分比到小數第二位；
100 代表期間內平均用滿一個核心。只讀取，不影響被量測的程序。

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

- device_monitor.py：進入點；解析命令列、設定日誌後啟動看板。--version 不載入 GTK。
- sdm/paths.py：安裝位置與 XDG 路徑；sdm/constants.py：更新間隔、逾時與上限。
- sdm/core/：純邏輯，不載入 GTK。fmt 格式化；counters 計數器；
  devices 電量等級與裝置正規化；settings 與 preference_specs 設定；
  jsonfile JSON 讀取與原子寫入；power_breakdown 電力拆解；ema 3 分鐘平均；
  history 歷史紀錄格式、保留與圖表降採樣；chart_layout 圖表與流向圖幾何；
  battery 筆電電池快照（多顆合併、充電判斷、系統耗電）；presentation 充電狀態與流向的顯示濾波；
  estimates 剩餘與充電時間、近 3 分鐘平均；recap 今日電量回顧。
- sdm/data/：唯讀資料來源，可指向假的 /sys 目錄測試。collectors 讀取 /proc、/sys、
  BlueZ、UPower；ups 解析 apcaccess；gpu 為常駐 nvidia-smi 串流與 AMD sysfs；
  machine 讀機型與開機時間；sources 為共用讀檔與命令；
  logitech_battery 為選用 Solaar CLI、G304／G305 電量解析及短期記憶體快取；
  power_supply 讀筆電電池與變壓器；upower 讀 UPower 預估與電源模式。
- sdm/services/：autostart 登入自啟、桌面入口與啟動器腳本；
  power_service 每次慢速收集產生耗電快照（有系統電池時為筆電快照，見 battery_service）；
  power_refresh 插拔後的重新讀取排程；history_service 歷史檔讀寫與損壞備份；
  preview 示範模式的資料；dump 為 --dump 自我檢查。
- sdm/ui/：GTK 介面。app 為視窗、彩色指示器及背景排程；pages/power 為「電源」頁；
  power_flow 電力流向圖；charts 耗電歷史圖；power_text 頁面文字（不載入 GTK）；
  pages/laptop_power、battery_flow、battery_text、battery_details 為筆電版面與文字；
  power_tray、power_wiring 為頂端列「耗電」與耗電快照的接線；smoke 為模擬測試；
  widgets 為共用元件與曲線圖；theme 為配色與 CSS；cpu_power_wiring、admin_text、
  settings/admin 為管理員 CPU 功耗取樣的接線、文字與設定分類。
- 管理員元件：helper/sdm-cpu-power（pkexec 執行的開關小程式）、helper/sdm-cpu-power-service
  （socket 啟動的 RAPL 取樣服務）、helper/policy（polkit 動作）、helper/systemd（單元檔，
  .deb 與原始碼安裝各一組）、packaging/debian（安裝前後腳本）、scripts/install_system.py
  （原始碼安裝的 root 端）。它們只用標準函式庫、不載入 sdm；常數與 sdm/constants.py 的
  CpuPower 相同（測試確認）。sdm/services/privileged.py 尋找元件並以 pkexec 開關，
  sdm/services/system_components.py 為 install.py --system-components 的使用者端，
  sdm/data/cpu_power_client.py 為 socket 客戶端，sdm/core/cpu_power.py 與 ppt_calibration.py
  為 CPU 項目與 APU PPT 校正。威脅模型見 SECURITY.md。

設定分成兩個檔案。settings.json 維持舊格式，只存五個頂端列開關，
舊版與退回舊版都能照常讀寫；新設定存在同目錄的 preferences.json（schema 2），
舊版不會讀取或覆寫。首次啟動只依 settings.json 是否存在判斷。
無效值改用預設值並在日誌記錄鍵名，不認得的鍵在儲存時保留；
兩個檔案都先寫暫存檔、fsync，再整檔取代。
preferences.json 無法解析時，先另存為新的 preferences-<時間>.json.bak（不覆寫既有備份）
再使用預設值；無法讀取、無法備份，或檔案來自較新版本（schema 較大）時，
本次執行只讀不寫，避免覆蓋或降級。載入時會清理本程式殘留超過一小時的暫存檔。

Solaar 為外部選用工具，沒有複製或封裝其原始碼；Piper 不參與看板執行。
可使用 ./install-dependencies.sh --logitech 安裝測試用工具。

慢速外部命令有逾時；不同資料來源獨立退回未知狀態，
不應把單一硬體失敗擴大為整個看板失效。
