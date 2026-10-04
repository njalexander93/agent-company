.PHONY: format format-check lint type-check test check

format:
	poetry run ruff format operations adapters tests

format-check:
	poetry run ruff format --check operations adapters tests

lint:
	poetry run ruff check --no-fix operations adapters tests

type-check:
	poetry run mypy

test:
	poetry run pytest --cov --cov-report=term-missing

check: format-check lint type-check test
