install:
	uv sync --all-extras

dev:
	uv run python manage.py runserver 0.0.0.0:8777

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

fmt:
	uv run ruff format && ruff check --fix
