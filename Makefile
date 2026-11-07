PYTHON ?= python3
PREFIX ?= $(HOME)/.local
CLI ?= ./scripts/agent-workbench

.PHONY: render validate test check update plan-hermes install-dry-run rust-release

render:
	$(CLI) render

validate:
	$(CLI) validate

test:
	$(PYTHON) -m unittest discover -s tests -v

check: test validate
	$(PYTHON) -m py_compile agent_workbench/*.py agent_workbench/adapters/*.py scripts/*.py

update:
	$(CLI) update --source cursor-plugins

plan-hermes:
	$(CLI) --profile pstack plan --target hermes

install-dry-run:
	$(PYTHON) scripts/install_skills.py --target agents --dry-run

rust-release:
	$(PYTHON) scripts/package_release.py --prefix "$(PREFIX)"
