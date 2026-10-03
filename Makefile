.PHONY: check test run format

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy

test:
	uv run pytest

run:
	uv run python -m app.main

format:
	uv run ruff format .
	uv run ruff check --fix .
