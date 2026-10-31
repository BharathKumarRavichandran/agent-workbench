PYTHON ?= python3

.PHONY: render validate test check update plan-hermes install-dry-run

render:
	$(PYTHON) -m agent_workbench render

validate:
	$(PYTHON) -m agent_workbench validate

test:
	$(PYTHON) -m unittest discover -s tests -v

check: test validate
	$(PYTHON) -m py_compile agent_workbench/*.py agent_workbench/adapters/*.py scripts/*.py

update:
	$(PYTHON) -m agent_workbench update --source cursor-plugins

plan-hermes:
	$(PYTHON) -m agent_workbench --profile pstack plan --target hermes

install-dry-run:
	$(PYTHON) scripts/install_skills.py --target agents --dry-run
