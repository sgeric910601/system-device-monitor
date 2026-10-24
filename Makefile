PYTHON := /usr/bin/python3

.PHONY: test import-check privacy deb source release-assets
test:
	$(PYTHON) -B -m unittest discover -s tests -t . -v
import-check:
	$(PYTHON) -B scripts/import_check.py --root .
privacy:
	$(PYTHON) -B scripts/privacy_check.py
	$(PYTHON) -B scripts/scan_secrets.py
deb:
	$(PYTHON) -B scripts/build_deb.py
source:
	$(PYTHON) -B scripts/release_assets.py --source-only
# 發布簽章：make release-assets SIGNING_KEY=<專案以外的私鑰>（或設定 SDM_RELEASE_SIGNING_KEY）
release-assets: deb
	$(PYTHON) -B scripts/release_assets.py $(if $(SIGNING_KEY),--signing-key "$(SIGNING_KEY)")
