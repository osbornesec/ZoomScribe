SHELL := /usr/bin/env bash

.PHONY: lint
lint:
	@set -euo pipefail; \
	ruff check .; \
	black --check .; \
	.venv/bin/python -m isort --check-only .; \
	.venv/bin/mypy src; \
	pytest -q; \
	sh_files=$$(git ls-files '*.sh'); \
	if [[ -n $$sh_files ]]; then \
		if command -v shellcheck >/dev/null 2>&1; then shellcheck $$sh_files; else echo "shellcheck not found, skipping"; fi; \
		if command -v shfmt >/dev/null 2>&1; then shfmt -d $$sh_files; else echo "shfmt not found, skipping"; fi; \
	else echo "No shell scripts to lint"; fi
