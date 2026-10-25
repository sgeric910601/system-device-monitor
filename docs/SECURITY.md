# 安全說明：管理員元件

看板本身一律以一般使用者執行，不需要也不接受 sudo。需要管理員權限的只有兩個選用功能：
「CPU 功耗取樣」（Linux 的 RAPL 能量計數器 /sys/class/powercap/intel-rapl:0/energy_uj 只有 root 能讀），
以及筆電的「充電上限」（/sys/class/power_supply/<電池>/charge_control_*_threshold 只有 root 能寫）。
這份文件說明為了這些功能安裝了什麼、每個元件能做什麼，以及刻意不做的事。

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

兩種安裝方式擇一：已安裝 .deb 時原始碼安裝會拒絕；.deb 安裝時若發現原始碼安裝的元件會提示移除。
不安裝任何 polkit 規則（/etc/polkit-1/rules.d）或 setuid 程式，安裝時也不啟用任何服務；
充電上限的單元只有在你開啟「開機與喚醒後保留」並授權後，才由小程式啟用。

## polkit 動作

- CPU 功耗取樣：.deb 為 tw.local.system-device-monitor.cpu-power，原始碼安裝為
  tw.local.system-device-monitor.local.cpu-power。只有本機作用中的工作階段可以授權
  （allow_active＝auth_admin），每次都要輸入管理員密碼（不是 auth_admin_keep）。
- 充電上限：.deb 為 tw.local.system-device-monitor.unattended.charge-limit，原始碼安裝為
  tw.local.system-device-monitor.unattended.local.charge-limit。同樣只有本機作用中的工作階段可以授權，
  預設是 auth_admin_keep：輸入管理員密碼後，polkit 的暫時授權只屬於執行 pkexec 的那個程序
  （看板），約 5 分鐘內同一個看板再次調整不必重新輸入，其他程式不能沿用。
- 非作用中的工作階段與遠端一律拒絕（allow_any、allow_inactive＝no）。
- 每個動作都綁定 org.freedesktop.policykit.exec.path＝小程式的絕對路徑，pkexec 只有在執行
  這個檔案時才使用這個動作。
- 開啟程式、開啟設定視窗都不會要求授權；只有使用者在設定裡切換「CPU 功耗取樣」、放開充電上限
  滑桿（或停 0.6 秒）、切換「開機與喚醒後保留」時才執行 pkexec。
- 動作 ID 的命名與自訂規則：
  - 管理員若要寫 polkit 規則（/etc/polkit-1/rules.d）放寬或收緊這些動作，請比對完整的動作 ID
    （例如 `action.id == "tw.local.system-device-monitor.cpu-power"`），不要用
    `indexOf("tw.local.system-device-monitor")` 之類的前綴比對，以免同時套用到之後新增的動作。
  - `tw.local.system-device-monitor.unattended.*` 這個前綴只用在「之後可以由使用者自己選擇加入的
    polkit 規則改成免密碼」的動作：目前是充電上限，之後還有智慧充電的定時喚醒。它們的預設仍然
    需要管理員密碼（auth_admin_keep），套件與原始碼安裝都不安裝任何規則。必須每次都輸入密碼的動作
    （例如本文件的 CPU 功耗取樣、之後的安裝更新）絕不使用這個前綴。

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
  區域名稱相符，計數上限 max_energy_range_uj（所有人可讀）直接讀取；之後只做 pread()。
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
  remove 只刪除它安裝的檔案、drop-in、sockets.target.wants 與充電上限單元在
  multi-user.target.wants／sleep.target.wants 的符號連結、原始碼版保留的充電上限
  （charge-limit-local.json，不碰 .deb 的 charge-limit.json）與充電上限小程式的鎖檔。
- 授權對象是 /usr/bin/python3（org.freedesktop.policykit.exec），對話框會顯示要以管理員執行
  Python 與它的參數；只在你自己執行這個指令時出現。

## 套件的安裝前後腳本

- postinst 只重新載入 systemd；socket 原本已啟用時才重新啟動它。從不自動啟用。
- prerm（移除時）停用並停止 socket 與服務，刪除 drop-in 與 sockets.target.wants 的連結；
  也停用充電上限的單元（刪除它的 wants 連結），並刪除 /var/lib/system-device-monitor/charge-limit.json
  （目錄空了才刪目錄，重新安裝時不會悄悄套回舊的上限）與 /run/system-device-monitor/charge-limit.lock
  （目錄空了才刪目錄）。
- postrm（purge 時）只刪除 drop-in 目錄裡本套件的檔案（目錄空了才刪目錄），並再確認一次
  charge-limit.json 已刪除；從不遞迴刪除目錄。
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

## 永遠不做的事

- 不安裝 setuid 程式或 polkit 規則；不在安裝時啟用任何東西（充電上限的單元只在你開啟保留時啟用）。
- 不在開啟程式時要求授權，也不記住或傳遞密碼。
- 不經過 shell 執行任何命令；不從使用者可寫的位置以 root 載入程式碼（原始碼安裝的暫存檔先驗證雜湊）。
- 取樣服務不連網路、不寫檔、不執行外部程式；小程式不寫入上表以外的位置，充電上限小程式在 sysfs
  只寫電池的兩個充電門檻。
- 不讀取或回傳 RAPL 以外的硬體資訊；不把 CPU 功耗寫進日誌或歷史檔。

## 已知限制

- 充電上限只以合成的 /sys 目錄驗證，尚未在真正的筆電上測試；重新套用的單元在 CI 中只確認能在
  真正的 systemd 下啟動（執行器沒有電池），/sys/devices 是否可寫依 systemd 文件的巢狀規則，
  還沒有在筆電上實測。
- 有些驅動只接受固定的值或會四捨五入，看板只能從讀回的值得知並說明；TLP、asusd 或 UPower 的
  充電門檻也可能在開機、喚醒或插拔電源時改回它們的值，看板只提醒、不阻止。

- 從登入自動啟動的看板可能不在 logind 的工作階段裡；polkit 124 會改用該使用者目前的
  圖形工作階段判斷是否「作用中」。若仍顯示「未授權」，請從應用程式選單重新開啟看板後再切換。
- 授權或小程式超過 2 分鐘沒有完成時，看板不再等待並嘗試結束 pkexec；已經以 root 執行的小程式
  一般使用者無法結束，會在 systemctl 的逾時內自行結束。
- 只讀第一個處理器插槽（intel-rapl:0）；多插槽伺服器的其他插槽不列入。
- 使用者私人群組的判斷依賴帳號與群組資料庫能完整列出：使用 sssd 等網域帳號而且
  enumerate=false 時，getpwall() 看不到其他網域帳號；群組成員也可能只記錄在 gshadow
  （或可用 newgrp 加入有密碼的群組），不會出現在 getgrgid() 的成員清單。這類環境請讓原始碼資料夾
  與 .git 群組不可寫（例如 chmod -R g-w），就不需要放寬。
- 原始碼安裝時，已經取得你帳號權限的攻擊者本來就能改你的原始碼資料夾或 install.py 本身
  （與 sudo make install 相同）；暫存後才被替換的檔案會因雜湊不符而被拒絕。只在你信任的原始碼
  資料夾、以沒有被入侵的帳號執行 install.py --system-components。
