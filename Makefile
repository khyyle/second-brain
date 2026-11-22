.DEFAULT_GOAL := help

sync: ## Install/refresh dependencies into the uv env
	uv sync

dev: ## Build the GUI and launch it with the local HTTP backend
	uv run python dev.py

gui-smoke: ## Test the Swift HTTP client against a temporary real backend
	uv run pytest src/second_brain/django_config/tests/test_gui_http.py --run-gui-http -q

gui-test: ## Run Swift tests (requires Xcode, not only Command Line Tools)
	DEVELOPER_DIR="$${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}" swift test --package-path gui/SecondBrainBar

backend: ## Prepare the vault and run the Django API (configuration from .env)
	uv run python start.py

backend-check: ## Check Django configuration without starting the server
	uv run python manage.py check

backend-test: ## Run the backend app and HTTP tests
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

.PHONY: sync dev gui-test gui-smoke backend backend-check backend-test install-hooks test lint format typecheck check help
