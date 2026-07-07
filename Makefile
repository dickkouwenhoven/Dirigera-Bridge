# ─────────────────────────────────────────────────────────────────────────────
# Makefile — Dirigera MQTT Bridge
#
# Usage:
#   make test           Run all tests
#   make test-unit      Run only unit tests
#   make test-integration Run only integration tests
#   make coverage       Run tests with coverage report
#   make coverage-html  Run tests and open HTML coverage report
#   make lint           Run ruff linter
#   make typecheck      Run mypy type checker
#   make clean          Remove generated files
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: test test-unit test-integration coverage coverage-html \
        lint typecheck install clean help

# Python interpreter — use python3 if python is not available
PYTHON     := python
PYTEST     := $(PYTHON) -m pytest
COVERAGE   := $(PYTHON) -m pytest --cov=app --cov-report=term-missing
COV_HTML   := $(PYTHON) -m pytest --cov=app --cov-report=html
RUFF       := $(PYTHON) -m ruff
MYPY       := $(PYTHON) -m mypy

# ── Test targets ──────────────────────────────────────────────────────────────

## Run all tests
test:
	$(PYTEST) tests/

## Run only unit tests (fast, no mocked layers)
test-unit:
	$(PYTEST) tests/ -m unit

## Run only integration tests (mocked multi-layer flows)
test-integration:
	$(PYTEST) tests/ -m integration

## Run a specific test file (usage: make test-file FILE=tests/core/test_errors.py)
test-file:
	$(PYTEST) $(FILE) -v

## Run tests matching a keyword (usage: make test-k KEY=lifecycle)
test-k:
	$(PYTEST) tests/ -k $(KEY) -v

# ── Coverage targets ──────────────────────────────────────────────────────────

## Run all tests with terminal coverage report
coverage:
	$(COVERAGE) tests/

## Run all tests and generate HTML coverage report in htmlcov/
coverage-html:
	$(COV_HTML) --cov-report=html tests/
	@echo ""
	@echo "Coverage report written to htmlcov/index.html"

# ── Code quality ──────────────────────────────────────────────────────────────

## Run ruff linter on app/ and tests/
lint:
	$(RUFF) check app/ tests/

## Run mypy type checker on app/
typecheck:
	$(MYPY) app/ --ignore-missing-imports --strict

# ── Setup ─────────────────────────────────────────────────────────────────────

## Install test dependencies
install:
	$(PYTHON) -m pip install \
		pytest \
		pytest-asyncio \
		pytest-cov \
		ruff \
		mypy

# ── Cleanup ───────────────────────────────────────────────────────────────────

## Remove generated test and build artefacts
clean:
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	rm -rf __pycache__/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Cleaned."

# ── Help ──────────────────────────────────────────────────────────────────────

## Show this help
help:
	@echo ""
	@echo "Dirigera MQTT Bridge — available make targets:"
	@echo ""
	@grep -E '^##' Makefile | sed 's/## /  /'
	@echo ""

