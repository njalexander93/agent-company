.DEFAULT_GOAL := help

.PHONY: help install validate-config format format-check lint type-check test test-unit test-integration check ci build clean

help: ## Show available development commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the package in editable mode and sync locked development dependencies.
	poetry sync

validate-config: ## Validate project metadata and lock consistency.
	poetry check --lock
	poetry run pre-commit validate-config

format: ## Apply Ruff formatting to source and tests.
	poetry run ruff format src tests

format-check: ## Check formatting without modifying files.
	poetry run ruff format --check --no-cache src tests

lint: ## Check Ruff rules without applying automatic fixes.
	poetry run ruff check --no-fix --no-cache src tests

type-check: ## Check production types using the root pyproject.toml policy.
	poetry run mypy

test: ## Run tests with branch coverage and the configured coverage floor.
	poetry run pytest --cov --cov-report=term-missing

test-unit: ## Run pure unit tests without imposing the aggregate coverage floor on a subset.
	poetry run pytest tests/unit

test-integration: ## Run stateful integration tests without a subset coverage gate.
	poetry run pytest tests/integration

check: validate-config format-check lint type-check test ## Run all local validation checks.

ci: check ## Run the local validation entry point for future CI integration.

build: validate-config ## Build a local wheel in dist/ without publishing it.
	poetry build --format wheel

clean: ## Remove generated output and caches; preserve .venv/ and .task/.
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache htmlcov
	rm -f .coverage .coverage.* coverage.xml coverage.json
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
