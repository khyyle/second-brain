.DEFAULT_GOAL := help

sync: ## Install/refresh dependencies into the uv env
	uv sync

backend: ## Prepare the vault and run the Django API (configuration from .env)
	uv run python start.py

backend-check: ## Check Django configuration without starting the server
	uv run python manage.py check

backend-test: ## Run the Django scaffold tests
	uv run pytest src/second_brain/apps src/second_brain/django_config/tests

install-hooks: ## Install pre-commit and commit-msg git hooks (run once after cloning)
	uv run pre-commit install && uv run pre-commit install --hook-type commit-msg

test: ## Run the full test suite
	uv run pytest

lint: ## Run the ruff linter
	uv run ruff check .

format: ## Auto-format and fix lint issues with ruff
	uv run ruff format . && uv run ruff check --fix .

typecheck: ## Run mypy (opt-in; may be noisy until annotations are clean)
	uv run mypy src

check: lint test ## Mirror CI: lint + tests

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: sync backend backend-check backend-test install-hooks test lint format typecheck check help
