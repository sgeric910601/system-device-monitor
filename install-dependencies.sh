#!/bin/bash
set -eu
sdm_install_logitech=false
case "$*" in
    "") ;;
    --logitech) sdm_install_logitech=true ;;
    *) echo "Usage: $0 [--logitech]" >&2; exit 2 ;;
esac

if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        gir1.2-gudev-1.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 \
        librsvg2-common util-linux \
        gnome-shell-extension-appindicator
    if "$sdm_install_logitech"; then
        sudo apt-get install -y solaar piper ratbagd
    fi
elif command -v dnf >/dev/null 2>&1; then
    # Fedora：套件名稱不同；GNOME 需要另外安裝 AppIndicator 擴充功能，KDE 內建系統匣支援。
    sudo dnf install -y python3 python3-gobject python3-cairo gtk3 libgudev \
        libayatana-appindicator-gtk3 libnotify librsvg2 util-linux
    # 以 gnome-shell 指令判斷 GNOME：XDG_CURRENT_DESKTOP 在 SSH、文字終端機或 sudo 底下是空的。
    # Fedora 裝好擴充功能後不會自動啟用；執行中的 GNOME 要重新登入才載入它，之後才能啟用。
    if command -v gnome-shell >/dev/null 2>&1; then
        sudo dnf install -y gnome-shell-extension-appindicator
        echo 'GNOME: log out and back in, then run this to show the top bar icon:'
        echo '    gnome-extensions enable "$(gnome-extensions list | grep appindicatorsupport)"'
    fi
    if "$sdm_install_logitech"; then
        sudo dnf install -y solaar piper libratbag-ratbagd
    fi
else
    echo "Unsupported distribution: need apt-get (Ubuntu/Debian) or dnf (Fedora)." >&2
    exit 1
fi
