SHELL := /usr/bin/env bash

.PHONY: lint
lint:
	@set -euo pipefail; \
	ruff check .; \
	black --check .; \
	isort --check-only .; \
	mypy src; \
	pytest -q; \
	sh_files=$$(git ls-files '*.sh'); \
	if [[ -n $$sh_files ]]; then shellcheck $$sh_files; else echo "No shell scripts to lint"; fi; \
	if [[ -n $$sh_files ]]; then shfmt -d $$sh_files; else echo "No shell scripts to format"; fi
