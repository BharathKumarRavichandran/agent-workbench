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
	cargo build --release --manifest-path rust/Cargo.toml
	mkdir -p "$(PREFIX)/bin"
	install -m 0755 rust/target/release/agent-workbench-rs "$(PREFIX)/bin/agent-workbench"
