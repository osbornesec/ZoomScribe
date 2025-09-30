SHELL := /usr/bin/env bash

.PHONY: lint
lint:
    @set -euo pipefail; \
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then PYTHON="$$VIRTUAL_ENV/bin/python"; \
    else PYTHON=$$(command -v python3 || command -v python || true); fi; \
    if [[ -z "$$PYTHON" ]]; then \
        echo "No Python interpreter found. Activate your virtualenv or install python3." >&2; \
        exit 1; \
    fi; \
    ruff check .; \
    black --check .; \
    "$$PYTHON" -m isort --check-only .; \
    "$$PYTHON" -m mypy src; \
    pytest -q; \
	sh_files=$$(git ls-files '*.sh'); \
	if [[ -n $$sh_files ]]; then \
		if command -v shellcheck >/dev/null 2>&1; then shellcheck $$sh_files; else echo "shellcheck not found, skipping"; fi; \
		if command -v shfmt >/dev/null 2>&1; then shfmt -d -i 4 -ci -sr $$sh_files; else echo "shfmt not found, skipping"; fi; \
	else echo "No shell scripts to lint"; fi
