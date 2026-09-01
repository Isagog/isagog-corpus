.PHONY: install test cov lint typecheck boundaries check staging demo

install:
	uv sync --group dev

test:
	uv run pytest

cov:
	uv run pytest --cov --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

boundaries:
	./scripts/check_boundaries.sh

# Everything CI runs.
check: lint typecheck cov boundaries

# The third contract run: against a live instance, keeping the schema honest.
staging:
	uv run pytest -m staging --override-ini="addopts="

# Run the example against a live instance; needs examples/.env (see examples/.env.example).
demo:
	uv run --extra examples python examples/latest_edition_authors.py
