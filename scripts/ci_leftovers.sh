#!/bin/bash
# 完全移除（apt-get purge）之後確認系統與裝置監看沒有留下任何東西；CI 在兩次 purge 之後執行。
# 用法：ci_leftovers.sh [--root 目錄] 套件.deb…
#   --root：檢查這個目錄底下（預設 /）。指向假的根目錄時不查詢 dpkg、也不用 sudo（測試使用）。
# 檢查每個 .deb 列出的檔案、本套件與管理員小程式自己的位置（小程式、polkit 動作、rules.d 的規則檔、
# systemd 單元、drop-in 與 wants 連結、/run、/var/lib、/var/cache 的狀態）與 dpkg 的紀錄。
# 每個留下的項目都印出「left behind: 路徑」，全部印完才以結束碼 1 結束；參數錯誤時結束碼 2。
# 最後在幾個系統目錄以名稱掃描，結果只是提示（note:），從不讓檢查失敗。
set -euo pipefail

NAME=system-device-monitor
USAGE="usage: $0 [--root DIR] PACKAGE.deb..."
root=/
if [ "${1:-}" = "--root" ]; then
    if [ $# -lt 2 ]; then echo "$USAGE" >&2; exit 2; fi
    root=$2
    shift 2
fi
if [ $# -eq 0 ]; then echo "$USAGE" >&2; exit 2; fi
for deb in "$@"; do
    if ! dpkg-deb --info "$deb" >/dev/null 2>&1; then echo "not a package: $deb" >&2; exit 2; fi
done
base=${root%/}
as_root=()
if [ -z "$base" ] && [ "$(id -u)" -ne 0 ]; then
    as_root=(sudo)       # rules.d 等目錄只有 root 能讀
fi
declare -A seen=()

report() {
    if [ -z "${seen[$1]+x}" ]; then
        seen[$1]=1
        echo "left behind: $1"
    fi
}

check_path() {
    # 存在（含指向不存在目標的符號連結）就算留下
    if "${as_root[@]}" test -e "$base$1" || "${as_root[@]}" test -L "$base$1"; then
        report "$1"
    fi
}

check_find() {
    # 目錄 $1 底下符合後面 find 條件的項目；目錄不存在時沒有結果
    local directory=$1 match
    shift
    while IFS= read -r match; do
        report "${match#"$base"}"
    done < <("${as_root[@]}" find "$base$directory" "$@" -print 2>/dev/null || true)
}

note_names() {
    # 只是提示：其他目錄裡名稱像本套件或小程式的項目（可能與套件無關，例如 systemd 自己的紀錄）
    local scan=() directory match
    for directory in /etc /usr/bin /usr/lib /usr/libexec /usr/share /usr/local/bin \
                     /usr/local/libexec /usr/local/share /var /run; do
        if [ -d "$base$directory" ]; then scan+=("$base$directory"); fi
    done
    if [ ${#scan[@]} -eq 0 ]; then return 0; fi
    while IFS= read -r match; do
        if [ -z "${seen[${match#"$base"}]+x}" ]; then
            echo "note: name match outside the checked locations (not a failure): ${match#"$base"}"
        fi
    done < <(timeout 300 "${as_root[@]}" find "${scan[@]}" \
                 \( -path "$base/run/user" -o -path "$base/var/lib/docker" -o -path "$base/var/cache/apt" \) \
                 -prune -o \( -name "*$NAME*" -o -name 'sdm-cpu-power*' -o -name sdm-charge-limit \
                 -o -name sdm-wake -o -name sdm-unattended -o -name sdm-update \) -print 2>/dev/null || true)
}

if [ -z "$base" ] && dpkg-query -W "$NAME" >/dev/null 2>&1; then
    report "dpkg still lists $NAME"
fi
for deb in "$@"; do
    while IFS= read -r entry; do
        path=/${entry#./}
        case "$path" in */) continue ;; esac
        check_path "$path"
    done < <(dpkg-deb --fsys-tarfile "$deb" | tar -t)
done
for path in /usr/libexec/$NAME /usr/local/libexec/$NAME /usr/share/$NAME /usr/share/doc/$NAME \
            /run/$NAME /var/lib/$NAME /var/cache/$NAME; do
    check_path "$path"
done
check_find /usr/share/polkit-1/actions -mindepth 1 -maxdepth 1 -name "tw.local.$NAME*"
check_find /usr/local/share/polkit-1/actions -mindepth 1 -maxdepth 1 -name "tw.local.$NAME*"
check_find /etc/polkit-1/rules.d -mindepth 1 -maxdepth 1 -name "*$NAME*"
check_find /usr/lib/systemd/system -mindepth 1 -maxdepth 1 -name "$NAME*"
check_find /etc/systemd/system -mindepth 1 -maxdepth 1 -name "$NAME*"
check_find /etc/systemd/system -mindepth 2 -maxdepth 2 -path "*.wants/$NAME*"
check_find /var/lib/dpkg/info -mindepth 1 -maxdepth 1 -name "$NAME.*"
note_names
if [ ${#seen[@]} -gt 0 ]; then
    echo "${#seen[@]} item(s) left behind after removing $NAME"
    exit 1
fi
echo "clean: no $NAME files remain"
