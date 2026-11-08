PYTHON ?= python3
PREFIX ?= $(HOME)/.local
CLI ?= ./scripts/agent-workbench

.PHONY: render validate test check update plan-hermes install-dry-run rust-release

render:
	$(CLI) render

validate:
	$(CLI) validate

test:
	cargo test --manifest-path rust/Cargo.toml

check: test validate

update:
	$(CLI) update --source cursor-plugins

plan-hermes:
	$(CLI) --profile pstack plan --target hermes

install-dry-run:
	$(CLI) --profile pstack plan --target agents

rust-release:
	$(PYTHON) scripts/package_release.py --prefix "$(PREFIX)"
