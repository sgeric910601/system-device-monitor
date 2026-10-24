# 安全說明：管理員元件

看板本身一律以一般使用者執行，不需要也不接受 sudo。唯一需要管理員權限的功能是選用的
「CPU 功耗取樣」：Linux 的 RAPL 能量計數器（/sys/class/powercap/intel-rapl:0/energy_uj）
只有 root 能讀。這份文件說明為了這個功能安裝了什麼、每個元件能做什麼，以及刻意不做的事。

## 要保護的東西與威脅

- 保護對象：root 權限、其他使用者的資料，以及 RAPL 計數器可能洩漏的資訊（見下方「RAPL 旁路」）。
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

兩種安裝方式擇一：已安裝 .deb 時原始碼安裝會拒絕；.deb 安裝時若發現原始碼安裝的元件會提示移除。
不安裝任何 polkit 規則（/etc/polkit-1/rules.d）、setuid 程式或開機自動啟用的服務。

## polkit 動作

- .deb：tw.local.system-device-monitor.cpu-power；原始碼安裝：tw.local.system-device-monitor.local.cpu-power。
- 只有本機作用中的工作階段可以授權（allow_active＝auth_admin），每次都要輸入管理員密碼
  （不是 auth_admin_keep）；非作用中的工作階段與遠端一律拒絕（allow_any、allow_inactive＝no）。
- 動作綁定 org.freedesktop.policykit.exec.path＝開關小程式的絕對路徑，pkexec 只有在執行
  這個檔案時才使用這個動作。
- 開啟程式、開啟設定視窗都不會要求授權；只有使用者在設定裡切換「CPU 功耗取樣」時才執行 pkexec。
- 動作 ID 的命名與自訂規則：
  - 管理員若要寫 polkit 規則（/etc/polkit-1/rules.d）放寬或收緊這些動作，請比對完整的動作 ID
    （例如 `action.id == "tw.local.system-device-monitor.cpu-power"`），不要用
    `indexOf("tw.local.system-device-monitor")` 之類的前綴比對，以免同時套用到之後新增的動作。
  - `tw.local.system-device-monitor.unattended.*` 這個前綴保留給之後不需要密碼的動作
    （第 8 階段的充電上限與定時喚醒），它們會有各自的限制與說明；需要管理員密碼的動作
    （例如本文件的 CPU 功耗取樣）絕不使用這個前綴。

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
- 服務執行期間在固定的單調時鐘刻度（啟動時間＋k 秒）讀一次計數器，與有沒有連線無關；
  每個連線拿到的都是最近一個已完成的 1 秒視窗平均，四捨五入到 0.1 W。請求從不觸發讀取，
  連線也無法選擇視窗的起點或拿到原始計數器。
- 不執行外部程式、不寫入任何檔案、不讀計數器以外的檔案。

## 原始碼安裝（install.py --system-components）

- 以一般使用者執行。原始碼資料夾、.git、實際的 git 目錄與共用 git 目錄，以及它們的每一層
  上層目錄，都必須屬於你或 root、其他人不可寫；群組可寫只接受 root 擁有且有黏著位元的目錄
  （/tmp），或你的使用者私人群組（與帳號同名、沒有其他成員、也不是任何其他帳號的主要群組，
  Ubuntu 預設 umask 002 建立的資料夾就是這樣）。否則別的帳號可能改掉 git 取出的內容。
  有 POSIX ACL 的項目（system.posix_acl_access，或目錄的 system.posix_acl_default）不套用這個
  放寬：有 ACL 時群組位元其實是 ACL 的遮罩，例如 `u:別人:rwx` 也會顯示成群組可寫；
  檔案系統不支援延伸屬性時視為沒有 ACL，其他無法確認的情況一律不放寬。
- git 目錄與共用 git 目錄裡的每一項（HEAD、config、packed-refs、refs、info、objects 等；
  hooks 不使用所以略過）都必須是同樣規則下的目錄或一般檔案，不接受符號連結或特殊檔案；
  objects/info/alternates、http-alternates、info/grafts 與 replace refs（refs/replace/ 或
  packed-refs 裡的 refs/replace/）會改變取出的內容，一律拒絕。
- 使用者端的 git 指令只讀取（rev-parse、ls-files、status、archive、log），而且以
  `-c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.allow=never` 執行，
  環境去掉所有繼承的 GIT_* 變數，再設 GIT_CONFIG_NOSYSTEM=1、GIT_CONFIG_GLOBAL=/dev/null、
  GIT_NO_REPLACE_OBJECTS=1、GIT_ATTR_NOSYSTEM=1：不執行 hooks、不連任何遠端、
  不讀系統或使用者層級的 git 設定。
- 要安裝的檔案必須已提交且沒有修改；以 git archive HEAD 取出，tar 裡只接受預期的一般檔案，
  內容由程式自己寫到新的私人暫存目錄，從不使用 tar 的解壓縮。暫存目錄放在 XDG_RUNTIME_DIR
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
  remove 只刪除它安裝的檔案、drop-in 與 sockets.target.wants 的符號連結。
- 授權對象是 /usr/bin/python3（org.freedesktop.policykit.exec），對話框會顯示要以管理員執行
  Python 與它的參數；只在你自己執行這個指令時出現。

## 套件的安裝前後腳本

- postinst 只重新載入 systemd；socket 原本已啟用時才重新啟動它。從不自動啟用。
- prerm（移除時）停用並停止 socket 與服務，刪除 drop-in 與 sockets.target.wants 的連結。
- postrm（purge 時）只刪除 drop-in 目錄裡本套件的檔案（目錄空了才刪目錄）；
  套件不建立任何其他狀態目錄，也從不遞迴刪除目錄。
- 以 dpkg --root 安裝到其他目錄（DPKG_ROOT）時不碰執行中的 systemd。

## RAPL 旁路

RAPL 能量計數器的變化可以用來推測同一台電腦上其他程式的運算內容（例如 2020 年的 PLATYPUS 攻擊，
CVE-2020-8694／8695），所以核心預設只讓 root 讀取。這個功能的緩解方式與剩下的風險：

- 預設關閉；開啟與關閉都需要管理員授權。
- 只有開啟它的那個帳號能連到 socket（0600＋SocketUser＋SO_PEERCRED）。但這個帳號底下
  任何沒有被沙箱隔離的程式（不只本看板，也包括指令稿與其他應用程式）都能連線，
  讀到和看板相同的數值。
- 只提供固定 1 秒視窗的平均、四捨五入到 0.1 W；所有連線共用同一個視窗，
  請求無法讓服務在指定的時間讀取，也拿不到原始的微焦耳計數器。
- 為什麼這比 PLATYPUS 粗糙得多：PLATYPUS 直接讀 energy_uj（微焦耳解析度，硬體依平台約每
  50 微秒到 1 毫秒更新一次），在受害程式的單一運算前後反覆讀取、累積數萬到數百萬次的統計，
  才分辨得出指令或資料（例如金鑰的位元）造成的微小差異。這裡的 1 秒平均把上千到兩萬次
  硬體更新合成一個數字，
  0.1 W 的量化（等於 1 秒內 0.1 焦耳）遠大於單一運算的能量差，而且讀取時間由服務自己的固定刻度
  決定，攻擊者無法對準受害程式的運算。
- 仍然看得到的是持續 1 秒以上、超過 0.1 W 的耗電變化（例如是否有重度工作在執行），
  這和一般使用者本來就能從 /proc 看到的 CPU 使用率屬於同一個層級。
- 看板只在「電源」頁顯示時連線；沒有連線 30 秒後服務結束。

多人共用、或會執行不信任程式的帳號不建議開啟。

## 永遠不做的事

- 不安裝 setuid 程式、polkit 規則或開機自動啟用的服務；不在安裝時啟用任何東西。
- 不在開啟程式時要求授權，也不記住或傳遞密碼。
- 不經過 shell 執行任何命令；不從使用者可寫的位置以 root 載入程式碼（原始碼安裝的暫存檔先驗證雜湊）。
- 取樣服務不連網路、不寫檔、不執行外部程式；小程式不寫入上表以外的位置。
- 不讀取或回傳 RAPL 以外的硬體資訊；不把 CPU 功耗寫進日誌或歷史檔。

## 已知限制

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
