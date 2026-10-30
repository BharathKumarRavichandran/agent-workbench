PYTHON ?= python3

.PHONY: render validate update install-dry-run

render:
	$(PYTHON) scripts/render_marketplaces.py

validate:
	$(PYTHON) scripts/validate.py

update:
	$(PYTHON) scripts/update.py

install-dry-run:
	$(PYTHON) scripts/install_skills.py --target agents --dry-run
