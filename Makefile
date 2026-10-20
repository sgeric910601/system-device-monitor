PYTHON := /usr/bin/python3

.PHONY: test privacy deb source release-assets
test:
	$(PYTHON) -B -m unittest discover -s tests -t . -v
privacy:
	$(PYTHON) -B scripts/privacy_check.py
	$(PYTHON) -B scripts/scan_secrets.py
deb:
	$(PYTHON) -B scripts/build_deb.py
source:
	$(PYTHON) -B scripts/release_assets.py --source-only
release-assets: deb
	$(PYTHON) -B scripts/release_assets.py
