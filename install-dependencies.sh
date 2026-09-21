#!/bin/bash
set -eu
sdm_install_logitech=false
case "$*" in
    "") ;;
    --logitech) sdm_install_logitech=true ;;
    *) echo "Usage: $0 [--logitech]" >&2; exit 2 ;;
esac
sudo apt-get update
sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-gudev-1.0 gir1.2-ayatanaappindicator3-0.1 librsvg2-common util-linux \
    gnome-shell-extension-appindicator

if "$sdm_install_logitech"; then
    sudo apt-get install -y solaar piper ratbagd
fi
