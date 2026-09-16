PYTHON := /usr/bin/python3

.PHONY: test privacy deb source release-assets
test:
	$(PYTHON) -m unittest discover -v
privacy:
	$(PYTHON) scripts/privacy_check.py
	$(PYTHON) scripts/scan_secrets.py
deb:
	$(PYTHON) scripts/build_deb.py
source:
	$(PYTHON) scripts/release_assets.py --source-only
release-assets: deb
	$(PYTHON) scripts/release_assets.py
