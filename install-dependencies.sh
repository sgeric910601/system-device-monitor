#!/bin/bash
set -eu
sudo apt-get update
sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-gudev-1.0 gir1.2-ayatanaappindicator3-0.1 librsvg2-common util-linux \
    gnome-shell-extension-appindicator
