# 安全說明：管理員元件、智慧充電與一鍵更新

看板本身一律以一般使用者執行，不需要也不接受 sudo。需要管理員權限的功能有三個：選用的
「CPU 功耗取樣」（Linux 的 RAPL 能量計數器 /sys/class/powercap/intel-rapl:0/energy_uj 只有 root 能讀）、
筆電的「充電上限」（/sys/class/power_supply/<電池>/charge_control_*_threshold 只有 root 能寫），
以及 .deb 安裝的一鍵更新（以 apt-get 安裝已簽章的新版本）。筆電的智慧充電另外用到兩個小程式：
設定 RTC 喚醒鬧鐘的 sdm-wake（「精準喚醒」，/sys/class/rtc/rtc0/wakealarm 只有 root 能寫），
以及只在你開啟「允許無人時自動調整」時才寫入一個 polkit 規則的 sdm-unattended。
這份文件說明為了這些功能安裝了什麼、每個元件能做什麼，以及刻意不做的事。
各元件的用途、安裝與移除方式見 [README](../README.md#選用的管理員元件) 與
[使用說明](USAGE.md#管理員元件)。

## 要保護的東西與威脅

- 保護對象：root 權限、其他使用者的資料、RAPL 計數器可能洩漏的資訊（見下方「RAPL 旁路」），
  以及電池的充電門檻（只能在你授權後、以驗證過的值改變）。
- 假設的攻擊者：這台電腦上的其他本機帳號、一般使用者權限的惡意程式，以及被竄改的安裝來源。
- 不在範圍內：已經取得你帳號權限的攻擊者在你輸入管理員密碼前竄改要執行的程式
  （與執行 sudo make install 時相同）、已經取得 root 的攻擊者。

## 安裝的檔案

| 檔案 | .deb 位置 | 原始碼安裝位置 | 擁有者與權限 |
|------|-----------|----------------|--------------|
| 開關小程式 sdm-cpu-power | /usr/libexec/system-device-monitor/ | /usr/local/libexec/system-device-monitor/ | root 0755 |
| 取樣服務 sdm-cpu-power-service（以臨時帳號執行） | 同上 | 同上 | root 0755 |
| polkit 動作 | /usr/share/polkit-1/actions/tw.local.system-device-monitor.policy | /usr/local/share/polkit-1/actions/tw.local.system-device-monitor.local.policy | root 0644 |
| socket 與服務單元 | /usr/lib/systemd/system/system-device-monitor-cpu-power.{socket,service} | /etc/systemd/system/system-device-monitor-local-cpu-power.{socket,service} | root 0644 |
| drop-in（開啟時才建立） | /etc/systemd/system/system-device-monitor-cpu-power.socket.d/owner.conf | /etc/systemd/system/system-device-monitor-local-cpu-power.socket.d/owner.conf | root 0644 |
| 充電上限小程式 sdm-charge-limit | /usr/libexec/system-device-monitor/ | /usr/local/libexec/system-device-monitor/ | root 0755 |
| polkit 動作（充電上限） | /usr/share/polkit-1/actions/tw.local.system-device-monitor.unattended.charge-limit.policy | /usr/local/share/polkit-1/actions/tw.local.system-device-monitor.unattended.local.charge-limit.policy | root 0644 |
| 重新套用充電上限的單元 | /usr/lib/systemd/system/system-device-monitor-charge-limit.service | /etc/systemd/system/system-device-monitor-local-charge-limit.service | root 0644 |
| 保留的充電上限（開啟保留時才建立） | /var/lib/system-device-monitor/charge-limit.json | /var/lib/system-device-monitor/charge-limit-local.json | root 0644（目錄 0755） |
| 定時喚醒小程式 sdm-wake | /usr/libexec/system-device-monitor/ | /usr/local/libexec/system-device-monitor/ | root 0755 |
| polkit 動作（喚醒的 set、clear） | /usr/share/polkit-1/actions/tw.local.system-device-monitor.unattended.wake.policy | /usr/local/share/polkit-1/actions/tw.local.system-device-monitor.unattended.local.wake.policy | root 0644 |
| 無人授權開關小程式 sdm-unattended | /usr/libexec/system-device-monitor/ | /usr/local/libexec/system-device-monitor/ | root 0755 |
| polkit 動作（無人授權開關與查詢） | /usr/share/polkit-1/actions/tw.local.system-device-monitor.rules.policy | /usr/local/share/polkit-1/actions/tw.local.system-device-monitor.local.rules.policy | root 0644 |
| 無人授權的 polkit 規則（只在你開啟時建立） | /etc/polkit-1/rules.d/49-system-device-monitor.rules | 同左 | root 0644 |
| 喚醒的紀錄、鎖與寫入時間（執行期） | /run/system-device-monitor/wake、wake.lock、wake.times、unattended.lock | 同左 | root 0600 |
| 更新小程式 sdm-update | /usr/libexec/system-device-monitor/ | （不提供） | root 0755 |
| polkit 動作（更新） | /usr/share/polkit-1/actions/tw.local.system-device-monitor.update.policy | （不提供） | root 0644 |
| 更新的暫存目錄與執行鎖（安裝更新時才建立，用完清空，只留 .lock） | /var/cache/system-device-monitor/staging、staging/.lock | （不提供） | root 0700、鎖 0600 |

兩種安裝方式擇一：已安裝 .deb 時原始碼安裝的 install 與 remove 都會拒絕（兩者共用無人授權的
規則檔與喚醒的紀錄，原始碼版的 remove 會撤銷套件的規則並取消它的鬧鐘；使用者端與 root 端各確認一次）。
.deb 安裝時若發現原始碼安裝的元件，postinst 只顯示警告與正確的順序（先移除套件、再執行原始碼版的
remove、之後重新安裝套件），不自動刪除任何東西。
套件與原始碼安裝都不安裝任何 polkit 規則或 setuid 程式，安裝時也不啟用任何服務；
充電上限的單元只有在你開啟「開機與喚醒後保留」並授權後，才由小程式啟用；
無人授權的規則只有在你開啟「允許無人時自動調整」並輸入管理員密碼後，才由 sdm-unattended 寫入。

## polkit 動作

- CPU 功耗取樣：.deb 為 tw.local.system-device-monitor.cpu-power，原始碼安裝為
  tw.local.system-device-monitor.local.cpu-power。只有本機作用中的工作階段可以授權
  （allow_active＝auth_admin），每次都要輸入管理員密碼（不是 auth_admin_keep）。
- 充電上限：.deb 為 tw.local.system-device-monitor.unattended.charge-limit，原始碼安裝為
  tw.local.system-device-monitor.unattended.local.charge-limit。同樣只有本機作用中的工作階段可以授權，
  預設是 auth_admin_keep：輸入管理員密碼後，polkit 的暫時授權只屬於執行 pkexec 的那個程序
  （看板），約 5 分鐘內同一個看板再次調整不必重新輸入，其他程式不能沿用。只有 set 使用這個動作
  （exec.argv1＝set）；`pkexec sdm-charge-limit status` 改用一般的 org.freedesktop.policykit.exec。
- 定時喚醒：set 為 tw.local.system-device-monitor.unattended.wake（argv1＝set），clear 為
  tw.local.system-device-monitor.unattended.wake-clear（argv1＝clear），原始碼安裝為
  …unattended.local.wake 與 …unattended.local.wake-clear；auth_admin_keep。
- 無人授權開關：tw.local.system-device-monitor.rules（原始碼安裝 …local.rules），auth_admin，每次都要密碼；
  查詢用的 …unattended.status（…unattended.local.status）預設一律拒絕（allow_active＝no）。
- 一鍵更新（只有 .deb）：tw.local.system-device-monitor.update，allow_active＝auth_admin，
  每次都要輸入管理員密碼（見下方「一鍵更新與發布簽章」）。
- 非作用中的工作階段與遠端一律拒絕（allow_any、allow_inactive＝no）。
- 每個動作都綁定 org.freedesktop.policykit.exec.path＝小程式的絕對路徑，pkexec 只有在執行
  這個檔案時才使用這個動作。
- 開啟程式、開啟設定視窗都不會要求授權；只有使用者在設定裡切換「CPU 功耗取樣」、放開充電上限
  滑桿（或停 0.6 秒）、切換「開機與喚醒後保留」或按「安裝更新」時才執行 pkexec。
- 動作 ID 的命名與自訂規則：
  - 管理員若要寫 polkit 規則（/etc/polkit-1/rules.d）放寬或收緊這些動作，請比對完整的動作 ID
    （例如 `action.id == "tw.local.system-device-monitor.cpu-power"`），不要用
    `indexOf("tw.local.system-device-monitor")` 之類的前綴比對，以免同時套用到之後新增的動作。
  - `tw.local.system-device-monitor.unattended.*` 這個前綴只用在「可以由使用者自己選擇加入的
    polkit 規則改成免密碼」的動作：充電上限的 set、定時喚醒的 set 與 clear，以及沒有作用的查詢動作。
    它們的預設仍然需要管理員密碼（auth_admin_keep；查詢動作一律拒絕），套件與原始碼安裝都不安裝任何
    規則。必須每次都輸入密碼的動作（CPU 功耗取樣、安裝更新、無人授權開關本身）絕不使用這個前綴。
  - 使用者自己開啟的免密碼規則（見下方「無人授權」）同時符合：
    - 只對本機、作用中的工作階段生效（`subject.local && subject.active`），而且只對開啟它的那個帳號
      （比對 `subject.user`）；遠端、非作用中或其他帳號一律回到預設（要密碼）。
    - 比對完整的動作 ID，例如 `action.id == "tw.local.system-device-monitor.unattended.charge-limit"`
      （原始碼安裝為 `...unattended.local.charge-limit`），不使用前綴比對。
    - 只涵蓋 set（與喚醒的 clear）：以 `org.freedesktop.policykit.exec.argv1` 註記讓 set 使用自己的
      動作（argv1＝set），status 改用一般的 org.freedesktop.policykit.exec（每次都要密碼）；規則另外確認
      `action.lookup("command_line")` 以「小程式的完整路徑＋空白＋set＋空白」開頭（clear 必須完全是
      「路徑＋空白＋clear」）。restore 本來就無法經由 pkexec 執行。
    - 免密碼不放寬小程式的任何檢查：參數驗證、一次只執行一個與同一顆電池 10 秒的寫入間隔都照常生效，
      而且不接受起點，只能寫 50–100% 的上限與開關保留。

## 開關小程式（sdm-cpu-power）

- 以 #!/usr/bin/python3 -IB 執行：隔離模式、不讀環境變數與使用者的 site-packages、不寫位元組碼；
  只用標準函式庫，不載入專案裡的任何模組。
- 用法固定為 enable、disable、status 其中一個，或不需要權限的 --protocol；其他參數一律以結束碼 2 拒絕。
- 除了 --protocol，必須以 root 執行，而且 PKEXEC_UID 必須是十進位、在 /etc/login.defs 的
  UID_MIN～UID_MAX 範圍內（讀不到時為 1000～60000）、不是 root 或 nobody、帳號存在的使用者，
  否則結束碼 4。
- 執行路徑（sys.argv[0]，不做任何正規化）必須與兩個安裝位置之一一字不差；pkexec 依這個字串比對
  exec.path，路徑寫法不同時會改用一般的 org.freedesktop.policykit.exec 動作，所以一律拒絕。
  依位置決定單元名稱；自己、單元檔與路徑上的每一層目錄都必須屬於 root 且群組與其他人不可寫，
  否則結束碼 3。
- 所有目錄都從 / 逐層以 O_NOFOLLOW 開啟；drop-in 先寫同目錄的暫存檔（O_EXCL、O_NOFOLLOW）、
  fsync 後整檔取代，既有的 owner.conf 不是一般檔案時拒絕。
- enable：寫入 drop-in（SocketUser＝PKEXEC_UID、SocketMode=0600），重新載入 systemd，
  停止服務、啟用並重新啟動 socket；disable：停用並停止 socket 與服務、刪除 drop-in、重新載入。
- 只以參數清單執行 /usr/bin/systemctl（不經過 shell），環境變數只有固定的 PATH 與 LANG。
- 標準輸出只有一行 JSON；結束碼固定（0 成功、2 參數錯誤、3 失敗、4 沒有權限）；
  系統日誌只記錄動作名稱與結果，不記錄使用者或路徑。

## 取樣服務（sdm-cpu-power-service）

- 只由 socket 啟動（Accept=no），沒有 [Install]，不會開機自動執行。
- 從不以 root 執行：DynamicUser=yes（帳號 sdm-cpu-power，uid 由 systemd 臨時配發），
  CapabilityBoundingSet= 與 AmbientCapabilities= 都是空的，所以沒有任何能力，也無法再取得。
- 只有 root 能讀的 energy_uj 由 systemd（PID 1）依 OpenFile= 以唯讀開好交給服務：
  intel-rapl:0（名稱 package）與 intel-rapl:0:0（名稱 core），加上 graceful，沒有 RAPL 的電腦
  直接略過。服務依 LISTEN_FDNAMES 找出 listen、package、core 三個描述子（名稱或數量不符就結束），
  確認計數器描述子正是 /sys/class/powercap 中對應區域的 energy_uj（比對裝置與 inode）而且
  區域名稱相符，計數上限 max_energy_range_uj（所有人可讀）直接讀取；之後只做 `pread()`。
- 啟動時的防禦性斷言：/proc/self/status 必須顯示不是 root、CapInh／CapPrm／CapEff／CapBnd／CapAmb
  全部為 0、NoNewPrivs 為 1，否則以結束碼 4 結束；所以在 systemd 之外（root 或一般使用者）
  直接執行一律拒絕。
- 單元的強化設定：NoNewPrivileges、PrivateNetwork、PrivateDevices、PrivateTmp、PrivateIPC、
  ProtectSystem=strict、ProtectHome、ProtectKernelTunables／Modules／Logs、ProtectControlGroups、
  ProtectClock、ProtectHostname、ProtectProc=invisible、ProcSubset=pid、RestrictAddressFamilies=AF_UNIX、
  RestrictNamespaces、SystemCallFilter=@system-service 減去 @resources、SystemCallArchitectures=native、
  MemoryDenyWriteExecute（CPython 不需要同時可寫可執行的記憶體，已用 strace 確認）、
  MemoryMax=64M、TasksMax=4、LockPersonality、RestrictRealtime、RestrictSUIDSGID、UMask=0077。
  systemd-analyze security 的曝險分數為 0.6（CI 要求不超過 1.0）。
- socket 檔權限 0600，擁有者由 drop-in 設為開啟這個功能的使用者；服務另外以 SO_PEERCRED 確認
  連線者是 socket 擁有者（或 root），其他連線立即關閉。
- 通訊協定只有一種請求：一行「r」；回一行 JSON（版本、狀態、package 與 core 瓦數、間隔）。
  其他任何輸入立即斷線；最多 2 個連線；每秒最多接受 4 個連線（超過時暫停接受，
  連線留在核心的佇列裡）；連線 30 秒沒有請求就關閉；沒有連線 30 秒後服務結束。
- 1 秒刻度的相位在服務啟動時決定（啟動時間＋k 秒），服務執行期間與有沒有連線無關地在
  刻度上讀一次計數器；讀取只發生在刻度上，或處理請求時補上剛過去的刻度。每個連線拿到的
  都是最近一個已完成的 1 秒視窗平均，四捨五入到 0.1 W，拿不到原始計數器。
  同一個帳號的客戶端可以等服務閒置 30 秒結束後重新連線、讓 systemd 重新啟動服務，
  藉此選擇新的相位，但拿到的仍然是 1 秒、0.1 W 的平均。
- 不執行外部程式、不寫入任何檔案、不讀計數器以外的檔案。

## 充電上限小程式（sdm-charge-limit）

- 以 #!/usr/bin/python3 -IB 執行，只用標準函式庫，不載入專案裡的任何模組。
- 用法固定：`set <電池> <上限> [--persist on|off]`、`status`、
  `restore --from-unit`，或不需要權限的 `--protocol`。在檢查權限之前先驗證參數，不符一律結束碼 2：
  - 電池名稱：`^[A-Za-z0-9_]{1,32}$`（BAT0、BATT、CMB0…；不接受 /、..、空白或非 ASCII）。
  - 上限：`^(5[0-9]|[6-9][0-9]|100)$`，也就是 50–100 的十進位整數，不接受前導 0、正負號、
    空白、小數或指數（49、101、-80、79.5、8e1、 80、080、1e2 都拒絕）。
  - `--persist` 最多一次，只接受 on 或 off。不接受起點（沒有 --start）：起點只在驅動需要時
    由小程式自動計算，呼叫者無法指定。
- set 與 status 必須以 root 執行，而且 PKEXEC_UID 的規則與 CPU 功耗開關相同（十進位、在
  /etc/login.defs 的 UID_MIN～UID_MAX 之間、不是 root 或 nobody、帳號存在），否則結束碼 4。
- restore 只給重新套用的單元使用：必須是 root、沒有 PKEXEC_UID、有 systemd 設定的 INVOCATION_ID
  （32 位小寫十六進位）而且父程序是 PID 1；經由 pkexec 執行一律拒絕（結束碼 4）。
- 執行路徑（sys.argv[0]，不正規化）必須與兩個安裝位置之一一字不差，依位置決定單元與狀態檔；
  自己、單元檔與路徑上每一層目錄都必須屬於 root 且群組與其他人不可寫，否則結束碼 3。
- 一次只執行一個：set、status 與 restore 在讀取門檻之前，先以 flock 取得
  /run/system-device-monitor/charge-limit.lock 的獨占鎖。鎖檔是 root 擁有、0600 的一般檔案
  （以 O_NOFOLLOW 開啟；權限或擁有者不符時拒絕），其他帳號無法開啟它來佔住鎖；目錄與 CPU 功耗的
  socket 共用（root、0755，從 /run 逐層確認）。別的小程式拿著鎖時每 0.1 秒再試，10 秒後以結束碼 3
  （busy）放棄。所以看板、命令列與開機後的重新套用不會交錯寫入。
- 寫入間隔：同一顆電池兩次 set 至少相隔 10 秒。找到電池後、寫入門檻之前（在鎖裡），就把開機後
  的時鐘（CLOCK_BOOTTIME，睡眠也會計時）記在同一目錄的 charge-limit.times（root、0600，整檔取代，
  只留最近一小時的紀錄），所以不論結果或是否被中斷都算一次；太快的呼叫以結束碼 3（rate-limited，
  附 retry_after 秒數）拒絕，不讀也不寫門檻。所有電池合計，最近一小時已經寫入 30 次時也以結束碼 3
  （hourly-limit）拒絕（滑動視窗，同一把鎖、同一個檔案；找不到電池等在寫入前就被拒絕的呼叫不計入）。
  restore 與 status 不受限制也不計入。執行期間忽略終端機的 SIGINT、SIGTSTP、SIGQUIT 與 SIGHUP，
  仍收到 KeyboardInterrupt 時回報 interrupted（結束碼 3、一行 JSON）：在終端機按 Ctrl-C 不能
  繞過這兩個限制。看板自己會等滿 10 秒才執行小程式，使用者一小時調整不到幾次、智慧充電一小時最多提高
  與還原各一次，正常操作不會碰到這兩個限制。這是為了免密碼的規則：即使不必輸入密碼，也不能讓
  其他程式反覆改寫門檻（頻繁寫入電池的控制晶片）或反覆啟用、停用單元。
- 電池檢查：/sys/class/power_supply/<電池> 的實際位置（realpath）必須在 /sys/devices 之下；
  type 必須是 Battery、scope 不是 Device（滑鼠等周邊），而且有 charge_control_end_threshold。
  所有屬性檔都在電池目錄的描述子底下以 O_NOFOLLOW｜O_NONBLOCK 開啟，必須是一般檔案。
- 寫入順序：上限不高於目前的起點時（驅動要求起點小於上限），先把起點改成上限 − 5，再寫上限；
  否則只寫上限，不動起點。已經相同的值不再寫。
  門檻檔以 O_WRONLY｜O_NOFOLLOW｜O_NONBLOCK 開啟（不建立、不截斷），只寫十進位數字，
  之後讀回確認（最多 3 次、間隔 0.3 秒）；不符時結束碼 3，並回報實際的上限與起點。
  只寫 charge_control_start_threshold 與 charge_control_end_threshold，不碰其他屬性。
- 保留：只有寫入確認成功後才處理。`--persist on` 把這次的電池與上限寫成
  /var/lib/system-device-monitor/charge-limit.json（原始碼安裝為 charge-limit-local.json）：
  /var 與 /var/lib 必須已存在且受信任（不建立），自己的目錄以 0755 建立（不受 umask 影響），
  檔案先寫同目錄的暫存檔（O_EXCL、O_NOFOLLOW、0644）並 fsync，再整檔取代；既有的同名項目
  不是一般檔案時拒絕。之後執行 systemctl enable 啟用單元。`--persist off` 刪除狀態檔
  （目錄空了一併刪除），單元已啟用時執行 systemctl disable。沒有指定時不改變。
  使用者調整時看板一律明確指定 on 或 off；之後的自動寫入（智慧充電）一律不帶 --persist，
  不改變使用者保留的值與重新套用的單元，所以重新開機或喚醒後套回的永遠是使用者自己設定的值。
- 看板以一般權限判斷「開機與喚醒後保留」：狀態檔屬於這顆電池、而且 systemctl is-enabled
  回報單元已啟用才算開啟；兩者不一致時顯示「保留設定未生效」，不會自行修改任何東西。
- restore 與 status 讀狀態檔時，必須是 root 擁有、群組與其他人不可寫、4 KB 以內的一般檔案
  （O_NOFOLLOW），JSON 的鍵與值的格式完全相符（與參數相同的規則），否則視為無效。
- 只以參數清單執行 /usr/bin/systemctl（不經過 shell），環境變數只有固定的 PATH 與 LANG；
  restore 不執行任何外部程式。
- 標準輸出只有一行 JSON；結束碼固定（0 成功、2 參數錯誤、3 失敗或沒有套用、4 沒有權限）；
  系統日誌只記錄動作名稱與結果（例如 set ok、set not-applied），不記錄電池名稱、數值或使用者。

## 重新套用的單元（system-device-monitor-charge-limit.service）

- Type=oneshot，ExecStart＝小程式 restore --from-unit；ConditionPathExists＝狀態檔，沒有保留時不執行。
  TimeoutStartSec=30s、TimeoutStopSec=10s：卡住時由 systemd 結束，不會拖住開機或喚醒。
- [Install] WantedBy=multi-user.target sleep.target，並排在 multi-user.target 與 suspend、hibernate、
  hybrid-sleep、suspend-then-hibernate 之後：開機與每次從睡眠喚醒後各執行一次。
  套件與原始碼安裝都不啟用它，只有 `--persist on` 會啟用。
- 以 root 執行（sysfs 的門檻檔屬於 root、權限 0644，靠一般的擁有者權限寫入），但
  CapabilityBoundingSet= 與 AmbientCapabilities= 都是空的，沒有任何能力，也無法再取得。
- 強化設定：NoNewPrivileges、PrivateNetwork、IPAddressDeny=any、PrivateDevices、PrivateTmp、
  PrivateIPC、ProtectSystem=strict、ProtectHome、ProtectKernelTunables／Modules／Logs、
  ProtectControlGroups、ProtectClock、ProtectHostname、ProtectProc=invisible、ProcSubset=pid、
  RestrictAddressFamilies=AF_UNIX、RestrictNamespaces、SystemCallFilter=@system-service
  減去 @privileged 與 @resources、SystemCallArchitectures=native、MemoryDenyWriteExecute、
  MemoryMax=64M、TasksMax=4、LockPersonality、RestrictRealtime、RestrictSUIDSGID、UMask=0077。
- RuntimeDirectory=system-device-monitor（0755、RuntimeDirectoryPreserve=yes）：小程式的鎖檔所在的
  /run/system-device-monitor 在單元中可寫，停止時不刪除（裡面也有 CPU 功耗的 socket）。
- 其他檔案系統唯讀，只有 ReadWritePaths=/sys/devices 可寫（ProtectKernelTunables 讓 /sys 唯讀，
  可寫的路徑依 systemd 的規則巢狀在裡面；/sys/class/power_supply 底下只是指向 /sys/devices 的
  符號連結）。systemd-analyze security 的曝險分數為 0.7（以 root 執行是主要的扣分）。

## 定時喚醒小程式（sdm-wake）

智慧充電選「精準喚醒」時，在該開始充電前一分鐘以 RTC 喚醒鬧鐘叫醒電腦提高上限。

- 以 #!/usr/bin/python3 -IB 執行，只用標準函式庫，不執行任何外部程式。
- 用法固定：`set <Unix 時間>`、`clear`、`status`，或不需要權限的 `--protocol`。時間只接受 1–12 位
  ASCII 十進位數字（不接受前導 0、正負號、空白、小數、指數或其他語言的數字），不符一律結束碼 2
  （在檢查權限之前）。
- set、clear、status 都必須以 root 執行，PKEXEC_UID 的規則與其他小程式相同，否則結束碼 4；
  執行路徑必須與兩個安裝位置之一一字不差，自己與路徑上每一層目錄都屬於 root 且群組與其他人不可寫。
- 一次只執行一個（/run/system-device-monitor/wake.lock，root 0600，最多等 10 秒）；兩次 set
  至少相隔 10 秒（開機後的時鐘記在 wake.times，root 0600；太快時結束碼 3、rate-limited）。
  clear 不受這個間隔限制，關機、撤銷或移除前一定能立即取消；它只在鬧鐘就是記下的時間時寫一次 "0"，
  之後紀錄就刪除，所以寫入 RTC 的次數仍受 set 的間隔限制。最近一小時已經 set 30 次時以結束碼 3
  （hourly-limit）拒絕（滑動視窗，記在同一個 wake.times；clear 不計入）。
- RTC：/sys/class/rtc/rtc0 的實際位置必須在 /sys/devices 之下，wakealarm 必須是一般檔案（no-rtc）；
  硬體時鐘（since_epoch）與系統時間相差超過 5 分鐘時拒絕（rtc-offset：核心把 RTC 當成 UTC，
  硬體時鐘存本地時間的雙系統電腦會差好幾個小時）。
- set：時間（系統時鐘 CLOCK_REALTIME）必須在 60 秒後到 7 天內（out-of-range，在取得鎖之後檢查）；
  目前已經有不是這個小程式記下的鬧鐘時拒絕（alarm-in-use），絕不覆寫別人（例如 rtcwake）的鬧鐘。
  先寫 "0"，把時間記在 /run/system-device-monitor/wake（root 0600，O_EXCL 暫存檔＋fsync＋改名），
  再寫時間（O_WRONLY｜O_NOFOLLOW，不建立）並讀回：任何時刻停下來，RTC 上的鬧鐘都已記下或是空的，
  之後的 clear 與移除套件都能取消它。讀回不符（not-applied）或任何例外（包括中斷）時寫 "0" 取消，
  紀錄改成與 RTC 一致（取消也失敗時記下 RTC 上實際的時間）。
- clear：只有目前的鬧鐘等於記下的時間時才寫 "0"；鬧鐘已經響過或被別人換掉時只刪除紀錄。
- 執行期間忽略終端機的 SIGINT、SIGTSTP、SIGQUIT 與 SIGHUP（寫入 RTC 與紀錄時也是），不會停在
  一半或暫停時拿著鎖；仍收到 KeyboardInterrupt 時回報 interrupted（結束碼 3），輸出仍只有一行 JSON。
- 標準輸出只有一行 JSON；結束碼固定（0、2、3、4）；系統日誌只記錄動作與結果（例如 set ok），
  不記錄時間或使用者。
- 看板只在排程選了精準喚醒、喚醒元件可用而且已允許無人時自動調整時才執行它（否則睡眠前改用準備優先，
  並在電源頁說明原因）；與已排定的時間相差 5 分鐘以上才重排，不需要時取消，關機前也取消
  （部分電腦在關機狀態也會被 RTC 鬧鐘開機）。

## 無人授權（sdm-unattended 與規則檔）

「允許無人時自動調整」讓排程在你不在電腦前（例如半夜）也能調整充電上限與喚醒時間，不必輸入密碼。
你決定的範圍：只限你本人、只在這台電腦登入而且正在使用時、只限充電上限與定時喚醒，可以隨時撤銷。

- 開關小程式 sdm-unattended（以 #!/usr/bin/python3 -IB 執行，只用標準函式庫，不執行外部程式）：
  用法固定 `grant`、`revoke`、`status` 或 `--protocol`。它的動作 tw.local.system-device-monitor.rules
  （原始碼安裝為 tw.local.system-device-monitor.local.rules）是 allow_active＝auth_admin：每次都要
  管理員密碼、不記住，也不在 unattended 前綴底下，規則本身不會讓它免密碼。PKEXEC_UID、執行路徑、
  一次只執行一個（unattended.lock）的規則與其他小程式相同。
- grant：帳號名稱必須符合 `^[a-z_][a-z0-9_-]{0,31}$`，而且以名稱查回的 uid 必須就是 PKEXEC_UID
  （不符時 unsupported-user）。依固定的範本寫入 /etc/polkit-1/rules.d/49-system-device-monitor.rules
  （root 0644，O_EXCL 暫存檔＋fsync＋改名）：範本只代入這個帳號名稱（不可能含引號、反斜線或換行），
  以及註解裡記錄的 uid（只有數字，只用來在 status 比對），其他內容一律固定。/etc、polkit-1、
  rules.d 必須已存在，逐層以 O_NOFOLLOW 開啟並確認屬於 root、群組與其他人不可寫（Ubuntu 的
  rules.d 是 root:polkitd 0750）；既有的同名項目不是一般檔案時拒絕。
  同一時間只有一個帳號：另一個帳號（也要管理員密碼）grant 時取代。
- 寫入與刪除規則時與 sdm-wake 一樣忽略終端機的訊號；仍被中斷時刪除暫存檔、回報 interrupted。
- revoke 只刪除那一個檔案（一般檔案或符號連結，不跟隨）；status 與範本逐字比對，被改過時回報 modified；
  帳號名稱現在對應的 uid 與註解裡記錄的不同時（帳號刪除後以同一個名稱重建）回報 account_changed。
- 規則的內容（ES5，polkitd 124 以 duktape 執行）：
  - `subject.user !== user || subject.local !== true || subject.active !== true` 時不做任何決定
    （回到預設：需要密碼）；遠端、非作用中、其他帳號一律如此。
  - 只比對完整的動作 ID（逐一 `action.id !== id`，沒有前綴比對）：
    `action.id == "tw.local.system-device-monitor.unattended.charge-limit"` 與
    tw.local.system-device-monitor.unattended.wake、…unattended.wake-clear，以及原始碼安裝的
    …unattended.local.charge-limit、…unattended.local.wake、…unattended.local.wake-clear。
  - 對這些動作再確認 pkexec 傳來的細節：`action.lookup("program")` 必須是對應小程式的完整路徑、
    `action.lookup("user")` 必須是 root、`action.lookup("command_line")` 必須完全符合固定的樣式
    （ES5 正規表示式，從 ^ 到 $，沒有 m、g、i 旗標）：
    - 充電上限：`^路徑 set [A-Za-z0-9_]{1,32} (5[0-9]|[6-9][0-9]|100)$`，電池名稱與上限的寫法和
      sdm-charge-limit 本身的參數檢查完全相同（測試逐字比對兩邊）。
    - 喚醒：`^路徑 set [1-9][0-9]{0,11}$`（與 sdm-wake 的時間格式相同），或剛好 `^路徑 clear$`。
    - 多一個參數（例如 `--persist on`、`--persist off`）、多一個空白、尾端空白、換行、前導 0、
      超出範圍的值都不符合。所以「開機與喚醒後保留」的開關一律要密碼，免密碼只能改這次的上限。

    全部相符才回傳 polkit.Result.YES，其他情況不回傳任何值（不是 NO，所以仍可依預設輸入密碼）。
  - 兩個 unattended.status 動作（…unattended.status、…unattended.local.status）沒有任何對應的程式，
    預設一律拒絕；規則生效時才允許，看板以一般權限的 pkcheck 查詢它，知道目前是否已授權
    （規則檔所在的目錄只有 root 與 polkitd 能讀）。
  - 測試以 polkitd 實際使用的 libduktape 執行規則，逐一確認上述每個條件。
- 只涵蓋 set（與喚醒的 clear）：pkexec 124 支援 `org.freedesktop.policykit.exec.argv1` 註記
  （這台 Ubuntu 24.04 的 pkexec 124 已確認：字串與說明文件都有），所以充電上限的動作加上 argv1＝set、
  喚醒的 set 與 clear 各有一個動作；pkexec 依路徑找動作，第一個參數不符時改用一般的
  org.freedesktop.policykit.exec（每次都要密碼）。所以充電上限與喚醒的 status 不在免密碼的範圍。
  pkexec 也確實把 command_line（完整路徑＋以空白連接的參數）放進動作細節，但 pkexec 的說明文件提醒
  它不能單獨當成安全檢查（參數以空白連接，界線不明確）；這裡先靠 argv1 與完整的動作 ID，再以樣式
  完整比對 command_line 限制參數的個數與寫法。同一行可能來自不同的參數切分（例如把「BAT0 80」包成
  一個參數），但小程式只接受各自獨立的參數，其他切分一律以結束碼 2 拒絕，不會做任何事。
- 為什麼規則不能被放寬：規則檔由 root 依固定範本產生，看板與一般使用者都無法改寫（rules.d 只有 root
  能寫）；範本只有帳號名稱（與註解裡的 uid）會變，而且經過格式與 uid 的雙重確認；免密碼只對上面
  列出的完整動作 ID，每個動作又綁定固定的程式路徑與完整的命令列，CPU 功耗取樣、安裝更新與規則開關
  本身都不在其中。免密碼也不放寬小程式的任何檢查：參數驗證、一次只執行一個、10 秒的寫入間隔與
  每小時 30 次的上限都照常生效，充電上限只能寫 50–100%（不能開關保留、不接受起點），喚醒只能設定
  60 秒後到 7 天內、不覆寫別人的鬧鐘。
- 移除套件（prerm remove，與 postrm purge 再確認一次）、降級到沒有這個功能的版本（0.3.0~ 以前），
  或原始碼安裝的 remove 都會刪除這個規則檔。
- 剩下的風險：
  - 已允許時，你的帳號底下任何沒有被沙箱隔離的程式，在你登入而且正在使用電腦時，都能免密碼把
    充電上限改成 50–100% 之間的值或設定、取消 7 天內的喚醒時間（不能做別的事，也不能改保留的設定）。
    不需要時請關閉。
  - 「本機、作用中」由 polkitd 判斷：程式不屬於任何 logind 工作階段時（例如 systemd --user 服務，
    或從 SSH 以 systemd-run --user 啟動的程式），polkitd 124 以 sd_uid_get_display 找出同一個帳號
    目前的圖形工作階段，改用它的「本機、作用中」。所以你在電腦前使用時，能以你的帳號登入 SSH 的人
    也能透過使用者服務做上面兩件事；SSH 工作階段本身是遠端，直接執行時仍需要密碼。
  - 規則以帳號名稱比對（polkit 只提供名稱，沒有 uid）：帳號刪除後若以同一個名稱建立新帳號（不同
    uid），新帳號會沿用這條規則。刪除帳號前請先關閉；sdm-unattended status 在名稱對應的 uid 與規則
    記錄的不同時回報 `"account_changed":true`，這時請執行 revoke（看板只能以 pkcheck 查詢是否生效，
    讀不到規則檔，所以只會顯示「已允許」）。

## 原始碼安裝（install.py --system-components）

- 以一般使用者執行。原始碼資料夾、.git、實際的 git 目錄與共用 git 目錄，以及它們的每一層
  上層目錄，都必須屬於你或 root、其他人不可寫；群組可寫只接受 root 擁有且有黏著位元的目錄
  （/tmp），或你的使用者私人群組（與帳號同名、沒有其他成員、也不是任何其他帳號的主要群組，
  Ubuntu 預設 umask 002 建立的資料夾就是這樣）。否則別的帳號可能改掉 git 取出的內容。
  這個放寬只用在本機檔案系統上、而且沒有任何 system.* 延伸屬性的項目：POSIX ACL
  （system.posix_acl_access、目錄的 system.posix_acl_default）、NFSv4、rich 與 CIFS 的 ACL
  都在這個命名空間；有 ACL 時群組位元其實是 ACL 的遮罩，例如 `u:別人:rwx` 也會顯示成群組可寫。
  位在網路、叢集或 FUSE 檔案系統（依 /proc/self/mountinfo：nfs*、cifs、smb3、smbfs、fuse、
  fuse.*、fuseblk、9p、ceph、glusterfs 等）的項目，擁有者與權限可能由伺服器或 FUSE 程式決定，
  只看嚴格的權限位元。檔案系統不支援延伸屬性（ENOTSUP）時視為沒有 ACL；其他無法確認的情況
  （查詢失敗、找不到掛載點）一律不放寬。
- git 目錄與共用 git 目錄裡的每一項（HEAD、config、packed-refs、refs、info、objects 等；
  hooks 不使用所以略過）都必須是同樣規則下的目錄或一般檔案，不接受符號連結或特殊檔案；
  objects/info/alternates、http-alternates、info/grafts 與 replace refs（refs/replace/ 或
  packed-refs 裡的 refs/replace/）會改變取出的內容，一律拒絕。
- 使用者端的 git 指令只讀取（rev-parse、config --list、ls-tree、cat-file blob、log），而且以
  `-c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.allow=never
  -c core.attributesFile=/dev/null` 執行，環境去掉所有繼承的 GIT_* 變數，再設
  GIT_CONFIG_NOSYSTEM=1、GIT_CONFIG_GLOBAL=/dev/null、GIT_NO_REPLACE_OBJECTS=1、
  GIT_ATTR_NOSYSTEM=1：不執行 hooks、不連任何遠端、不讀系統或使用者層級的 git 設定。
- 本地（與 worktree）的 git 設定含 include.*、includeIf.*、filter.* 或 core.attributesFile 時
  一律拒絕（列出設定時指定檔案，不會展開 include）：這些設定可能在讀取時執行指令或改變內容。
- 要安裝的內容是 HEAD 中原始的 blob：以 ls-tree 找出物件 ID（只接受 100644／100755 的一般檔案，
  不接受符號連結或子模組），以 cat-file blob 讀出，再以物件 ID 驗證內容；不經過 git archive，
  不套用任何 filter、export-subst、ident 或換行轉換。工作樹是否有未提交的修改由程式自己計算
  工作樹檔案的物件 ID 比對（不執行 git status，不會觸發 clean filter）。
- 內容由程式自己寫到新的私人暫存目錄。暫存目錄放在 XDG_RUNTIME_DIR
  （屬於你、0700、上層安全），沒有時才用 root 擁有且有黏著位元的 /tmp；執行 pkexec 前
  再確認暫存目錄是你的 0700 目錄、每一層上層都安全。
- 執行 pkexec 前在終端機顯示要安裝的提交（短雜湊與標題）。
- root 從不以路徑讓 Python 執行使用者可寫的腳本：pkexec 執行的是
  /usr/bin/python3 -I -B -c <固定的短啟動碼> <暫存的 install_system.py> <它的 SHA-256> …。
  啟動碼確認 PKEXEC_UID、腳本路徑已正規化、每一層上層目錄都安全、腳本所在目錄屬於你且只有你
  能存取，以 O_NOFOLLOW｜O_NONBLOCK 開啟腳本、確認它是你擁有的一般檔案，讀一次、比對 SHA-256，
  再執行剛才比對過的同一份位元組。也就是說，root 只會執行雜湊相符的那份使用者暫存的程式碼。
- install_system.py（root 端）再確認暫存目錄與每一層上層、manifest 的雜湊、內容與每個檔案的
  雜湊（暫存檔一律 O_NOFOLLOW｜O_NONBLOCK 開啟），全部通過後才開始寫入；目的地與權限寫死在
  程式裡，路徑上每一層都必須屬於 root 且群組與其他人不可寫。任何錯誤都只輸出一行 JSON。
  remove 最先以 fcntl 取得 sdm-unattended 的鎖（unattended.lock）再撤銷無人授權的規則檔（只有那一個
  檔案），正在執行的 grant 不會在之後把它寫回來，也不會再有免密碼的 sdm-wake 開始；再取得 sdm-wake
  的鎖（wake.lock），等正在執行的 sdm-wake 結束才取消它記下的喚醒鬧鐘（紀錄必須是 root 擁有、
  其他人不可寫，而且目前的鬧鐘等於它）並刪除紀錄。兩把鎖都只開啟 root 擁有的既有一般檔案（不建立、
  不跟隨符號連結），最多等 15 秒，逾時仍繼續。接著只刪除它安裝的檔案、drop-in、sockets.target.wants
  與充電上限單元在 multi-user.target.wants／sleep.target.wants 的符號連結、原始碼版保留的充電上限
  （charge-limit-local.json，不碰 .deb 的 charge-limit.json）。小程式刪除之後再拿兩把鎖做一次
  （授權在規則刪除前就給了、之後才完成的 grant 或 sdm-wake set），拿著鎖刪除紀錄、鎖檔與寫入時間，
  最後刪除充電上限小程式的鎖檔與寫入時間。
- dpkg-query 的結束碼 1 是找不到套件；其他失敗（例如結束碼 2、無法執行）或看不懂的回答都無法判斷
  是否已安裝套件，install 與 remove 一律拒絕（package-check-failed），不會在不確定時動手。
- 授權對象是 /usr/bin/python3（org.freedesktop.policykit.exec），對話框會顯示要以管理員執行
  Python 與它的參數；只在你自己執行這個指令時出現。

## 套件的安裝前後腳本

- postinst 只重新載入 systemd；socket 原本已啟用時才重新啟動它。從不自動啟用。
- prerm（移除時）停用並停止 socket 與服務，刪除 drop-in 與 sockets.target.wants 的連結；
  也停用充電上限的單元（刪除它的 wants 連結），並刪除 /var/lib/system-device-monitor/charge-limit.json
  （目錄空了才刪目錄，重新安裝時不會悄悄套回舊的上限）。智慧充電依序：先以 flock 取得
  sdm-unattended 的鎖（unattended.lock）再刪除無人授權的規則檔（只刪那一個一般檔案或符號連結），
  再取得 sdm-wake 的鎖（wake.lock），才取消 sdm-wake 記下的喚醒鬧鐘（只有紀錄是一般檔案、內容是數字、
  而且目前的鬧鐘等於它時才寫 0，別人的鬧鐘不動）並刪除紀錄。鎖只在是一般檔案時才拿，最多等 15 秒，
  逾時仍繼續；鎖檔留給 postrm（刪除還有人在等的鎖檔會讓兩個小程式同時執行）。
- postrm（remove 時，小程式已經刪除）用同一段程式（prerm 與 postrm 的這一段必須完全相同，有測試
  比對）再拿兩把鎖、撤銷一次規則並取消鬧鐘（授權在規則刪除前就給了、之後才完成的 grant 或
  sdm-wake set），然後才刪除鎖檔、寫入時間與 charge-limit.lock、charge-limit.times。
- prerm（upgrade 時）不動任何東西，只有降級到沒有智慧充電的版本（`dpkg --compare-versions` 比
  0.3.0~ 舊；結尾的 ~ 讓 0.3.0~beta.N 也算有）時撤銷無人授權的規則、取消記下的喚醒鬧鐘並刪除紀錄，
  因為舊版不會再管理或撤銷它們。這一步盡力而為：失敗（例如 /etc 唯讀）只在標準錯誤說明，讓降級
  繼續（若讓 prerm 失敗，dpkg 會改跑舊版的 prerm failed-upgrade，規則反而留下）；postrm upgrade
  在檔案換成舊版之後再做一次。
- postrm（purge 時）只刪除 drop-in 目錄裡本套件的檔案（目錄空了才刪目錄），並再確認一次
  charge-limit.json 與無人授權的規則檔已刪除；從不遞迴刪除目錄。
- 以 dpkg --root 安裝到其他目錄（DPKG_ROOT）時不碰執行中的 systemd。

## RAPL 旁路

RAPL 能量計數器的變化可以用來推測同一台電腦上其他程式的運算內容（例如 2020 年的 PLATYPUS 攻擊，
CVE-2020-8694／8695），所以核心預設只讓 root 讀取。這個功能的緩解方式與剩下的風險：

- 預設關閉；開啟與關閉都需要管理員授權。
- 只有開啟它的那個帳號能連到 socket（0600＋SocketUser＋SO_PEERCRED）。但這個帳號底下
  任何沒有被沙箱隔離的程式（不只本看板，也包括指令稿與其他應用程式）都能連線，
  讀到和看板相同的數值。
- 只提供 1 秒視窗的平均、四捨五入到 0.1 W；所有連線共用同一個視窗，拿不到原始的
  微焦耳計數器。服務執行期間，請求無法讓服務在刻度以外的時間讀取；刻度的相位在服務啟動時
  決定，同一個帳號的程式可以等服務閒置結束後重新啟動它來換一個相位，最細也只是每秒一次。
- 為什麼這比 PLATYPUS 粗糙得多：PLATYPUS 直接讀 energy_uj（微焦耳解析度，硬體依平台約每
  50 微秒到 1 毫秒更新一次），在受害程式的單一運算前後反覆讀取、累積數萬到數百萬次的統計，
  才分辨得出指令或資料（例如金鑰的位元）造成的微小差異。這裡的 1 秒平均把上千到兩萬次
  硬體更新合成一個數字，
  0.1 W 的量化（等於 1 秒內 0.1 焦耳）遠大於單一運算的能量差；就算重新啟動服務換了相位，
  每個數字仍涵蓋整整 1 秒，對不準受害程式微秒到毫秒級的單一運算。
- 仍然看得到的是持續 1 秒以上、超過 0.1 W 的耗電變化（例如是否有重度工作在執行），
  這和一般使用者本來就能從 /proc 看到的 CPU 使用率屬於同一個層級。
- 看板只在「電源」頁顯示時連線；沒有連線 30 秒後服務結束。

多人共用、或會執行不信任程式的帳號不建議開啟。

## 一鍵更新與發布簽章（sdm-update，只有 .deb）

做法與 Sparkle（macOS 常見的更新框架）及維護者的 macOS 版相同：維護者以離線保管的 Ed25519 私鑰簽
每個 Release 的 SHA256SUMS，程式內建對應的公鑰；只有簽章與檢查碼都相符的 .deb 才會安裝。
格式相同，但金鑰必須是本專案專用：絕不沿用 macOS 版（Sparkle）的金鑰或任何其他產品的金鑰。
共用金鑰時，別的產品簽過的 SHA256SUMS 也能在這裡通過驗證，任何一邊外洩都會連累另一邊。

- 簽章格式：SHA256SUMS.sig 是一行 base64，內容是對 SHA256SUMS 檔案原始位元組的 64 位元組 Ed25519
  簽章。公鑰是 32 位元組原始公鑰的 base64，放在陣列裡：sdm/constants.py 的 Update.PUBLIC_KEYS 與
  helper/sdm-update 的 PUBLIC_KEYS（測試確認兩者相同）；任何一把驗證通過即可。驗證時把公鑰組成
  SubjectPublicKeyInfo 的 PEM，與簽章一起寫在私人暫存目錄，以 /usr/bin/openssl pkeyutl -verify
  -pubin -rawin 驗證。陣列是空的時候（還沒產生正式金鑰的版本）一律拒絕安裝，設定頁顯示
  「未設定簽章金鑰」。公鑰只來自程式本身，絕不從環境變數、參數或設定檔讀取。
- 私鑰只在維護者自己的電腦上：以 scripts/release_keys.py 產生在任何 git 工作樹以外的 0700 資料夾
  （私鑰 0600），離線備份；不放進專案、CI、雲端同步資料夾或 GitHub Secrets。發布時在本機以
  make release-assets 簽章（離線簽章），腳本確認這把金鑰的公鑰列在 Update.PUBLIC_KEYS 裡，
  並以程式端同樣的驗證確認簽章後才寫出 SHA256SUMS.sig。make privacy 會拒絕專案資料夾裡
  （含被 .gitignore 忽略的）任何私鑰檔。
- 換金鑰：先發布一個公鑰陣列同時含新舊兩把的版本（仍以舊金鑰簽，新舊並存），之後的版本改用
  新金鑰簽，再找一個版本把舊公鑰移除。私鑰外洩時立刻從陣列移除那把公鑰並以新金鑰發布；
  還停在只認舊公鑰版本的使用者要手動安裝一次新版。私鑰遺失時同樣只能換新金鑰。
- 程式端（一般使用者）：只連 HTTPS 的 github.com、api.github.com 與 *.githubusercontent.com，
  每一次轉址都重新確認，有大小上限（API 1 MB、SHA256SUMS 64 KB、.sig 1 KB、.deb 30 MB）與逾時；
  先下載並驗證簽章，再下載 .deb、比對 SHA256SUMS 中這個檔名（完全相同）的雜湊，再以 dpkg-deb -f
  確認 Package、Architecture all、Version 與 Release 相同，並以 dpkg --compare-versions 確認比已安裝
  的版本新；最後才執行 pkexec。
- polkit 動作 tw.local.system-device-monitor.update：allow_active＝auth_admin（每次都要密碼，不是
  auth_admin_keep），allow_any、allow_inactive＝no；exec.path＝/usr/libexec/system-device-monitor/sdm-update。
  只有 .deb 安裝這個小程式與動作，原始碼安裝不提供（原始碼資料夾以 git 更新）。這個動作不在
  unattended 前綴底下，也不會被任何免密碼的規則涵蓋。
- sdm-update 以 root 執行時不相信程式端的檢查，自己再做一次：沿用開關小程式的規則
  （#!/usr/bin/python3 -IB、只用標準函式庫、執行路徑一字不差、PKEXEC_UID 是一般帳號、固定的 PATH、
  只以參數清單執行、一行 JSON、結束碼 0／2／3／4、系統日誌只記錄動作名稱）；參數固定為
  install [--allow-beta] <.deb> <SHA256SUMS> <SHA256SUMS.sig>，檔名必須完全符合。測試版（~beta）的
  套件只有加上 --allow-beta 才會往下檢查，否則以 channel 結束；看板只在使用者以「立即檢查」看到並
  選擇了測試版時才傳這個參數，正式版與自動檢查從不傳。三個檔案以
  O_NOFOLLOW｜O_NONBLOCK 開啟，fstat 必須是呼叫者擁有、不超過上限的一般檔案，經由描述子複製到
  /var/cache/system-device-monitor/staging/<亂數>/（root 0700，O_EXCL），之後只用這份 root 的複本。
  同一時間只有一個 sdm-update 在執行：整段期間持有 staging/.lock（root 0600、O_NOFOLLOW）的獨占
  flock，別人持有時最多等 10 秒，否則以 busy 結束；之前中斷留下的暫存檔只在持有這個鎖時清理，
  已安裝的版本也在鎖內讀取。接著以內建公鑰驗證簽章、
  比對雜湊、dpkg-deb -f 檢查欄位並確認比 dpkg-query 查到的已安裝版本新、
  dpkg-deb -c 確認每一項都只在本套件的位置（/usr/bin/system-device-monitor、
  /usr/share/system-device-monitor/、/usr/share/doc/system-device-monitor/、
  /usr/libexec/system-device-monitor/、/usr/share/applications/system-device-monitor*.desktop、
  /usr/share/polkit-1/actions/tw.local.system-device-monitor*、/usr/lib/systemd/system/system-device-monitor*、
  /usr/share/icons/hicolor/*/apps/system-device-monitor.png），只有一般檔案與目錄、擁有者 root/root、
  沒有 setuid／setgid／黏著位元、沒有群組或其他人可寫、沒有「..」。這份位置清單只是縱深防禦，把關的是簽章：
  維護者簽過的套件本來就能在安裝前後腳本以 root 執行任何程式，清單只是在簽章流程出錯時多一道限制。
  接著以 fcntl 等 dpkg 的前端鎖
  （最多 120 秒，忙碌時乾淨地失敗），以 apt-get install -y --no-install-recommends --no-remove
  （DEBIAN_FRONTEND=noninteractive）安裝暫存的複本，確認已安裝的版本後刪除複本。apt-get 在自己的
  程序群組執行，時限 30 分鐘；超過時只對整個群組送 SIGTERM、再等 60 秒，絕不送 SIGKILL（中途強制結束
  會讓 dpkg 停在只裝了一半的狀態），並回報 apt-timeout，設定頁提示執行 sudo dpkg --configure -a。
  套件不是以 .deb 安裝時直接拒絕。
- 為什麼簽章在小程式裡還要再驗一次：就算你的帳號被入侵、而且你輸入了管理員密碼，root 端也只會
  安裝維護者簽過的 .deb；被竄改的 GitHub 帳號或 Release 檔案同樣無法讓程式安裝沒有簽章的套件。
  不能降級，也不能重裝同一版。
- 簽章不能防的事：
  - 維護者的私鑰外洩（換金鑰的方式見上方）；新版本本身的程式錯誤。
  - 控制了 GitHub（帳號、Release 檔案或 API 回應）或網路、但沒有私鑰的人，可以隱藏比較新的版本，
    讓你一直停在目前的版本（看板只會顯示「已是最新版本」或檢查失敗）；也可以只提供某個
    比已安裝的新、但不是最新的已簽章版本（例如還有已知問題的舊版），看板仍會安裝它。簽章不含發布管道或時間，
    無法分辨這些情況：只保證不能降級、不能重裝同一版，測試版只在加上 --allow-beta（使用者在
    「立即檢查」選擇了測試版）時才安裝。對新版本有疑問時，請直接到 GitHub 的 Releases 頁面確認。
  - apt-get 會照常從系統設定的套件庫安裝新版本需要的相依套件，那些套件由 Ubuntu 的套件簽章保護。

## 永遠不做的事

- 不安裝 setuid 程式，也不隨套件安裝 polkit 規則（無人授權的規則只在你開啟時寫入，只給你的帳號）；
  不在安裝時啟用任何東西（充電上限的單元只在你開啟保留時啟用）。
- 不在開啟程式時要求授權，也不記住或傳遞密碼。
- 不經過 shell 執行任何命令；不從使用者可寫的位置以 root 載入程式碼（原始碼安裝的暫存檔先驗證雜湊）。
- 取樣服務不連網路、不寫檔、不執行外部程式；小程式不寫入上表以外的位置，充電上限小程式在 sysfs
  只寫電池的兩個充電門檻，喚醒小程式只寫 rtc0 的 wakealarm，無人授權小程式只寫一個規則檔。
- 更新小程式不連網路，只安裝簽章與檢查碼都相符、比已安裝版本新的 .deb；看板不在背景自動安裝，
  一定是你按「安裝更新」並輸入管理員密碼。
- 不讀取或回傳 RAPL 以外的硬體資訊；不把 CPU 功耗寫進日誌或歷史檔。

## 已知限制

- 充電上限只以合成的 /sys 目錄驗證，尚未在真正的筆電上測試；重新套用的單元在 CI 中只確認能在
  真正的 systemd 下啟動（執行器沒有電池），/sys/devices 是否可寫依 systemd 文件的巢狀規則，
  還沒有在筆電上實測。
- 智慧充電（排程、睡眠延遲、精準喚醒、無人授權）只以合成的資料、假的 logind 與私人匯流排驗證，
  尚未在真正的筆電上測試；RTC 喚醒在這台桌機與 CI 只做到參數與權限檢查（CI 的虛擬機有 RTC 時才
  實際排定再取消）。看板在即將睡眠時最多延後 4 秒；寫入沒完成就放開，喚醒後再補。
- 部分電腦關機後也會在 RTC 鬧鐘的時間開機：看板在關機前取消它，但看板沒有執行（或沒有允許無人時
  自動調整）時做不到，下一次開機或喚醒後看板會依排程重新判斷。
- 有些驅動只接受固定的值或會四捨五入，看板只能從讀回的值得知並說明；TLP、asusd 或 UPower 的
  充電門檻也可能在開機、喚醒或插拔電源時改回它們的值，看板只提醒、不阻止。

- 從登入自動啟動的看板可能不在 logind 的工作階段裡；polkit 124 會改用該使用者目前的
  圖形工作階段判斷是否「作用中」。若仍顯示「未授權」，請從應用程式選單重新開啟看板後再切換。
- 授權或小程式超過 2 分鐘沒有完成時，看板不再等待並嘗試結束 pkexec；已經以 root 執行的小程式
  一般使用者無法結束，會在 systemctl 的逾時內自行結束。
- 只讀第一個處理器插槽（intel-rapl:0）；多插槽伺服器的其他插槽不列入。
- 安裝更新時，看板一直等到 pkexec 真的結束才決定結果；超過 5 分鐘（含輸入密碼的時間）只改顯示
  「仍在安裝中…」，「安裝更新」與「立即檢查」維持停用，因為以 root 執行的安裝無法從看板中斷。
- 使用者私人群組的判斷依賴帳號與群組資料庫能完整列出：使用 sssd 等網域帳號而且
  enumerate=false 時，getpwall() 看不到其他網域帳號；群組成員也可能只記錄在 gshadow
  （或可用 newgrp 加入有密碼的群組），不會出現在 getgrgid() 的成員清單。這類環境請讓原始碼資料夾
  與 .git 群組不可寫（例如 chmod -R g-w），就不需要放寬。
- 原始碼安裝時，已經取得你帳號權限的攻擊者本來就能改你的原始碼資料夾或 install.py 本身
  （與 sudo make install 相同）；暫存後才被替換的檔案會因雜湊不符而被拒絕。只在你信任的原始碼
  資料夾、以沒有被入侵的帳號執行 install.py --system-components。
