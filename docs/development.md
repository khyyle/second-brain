# Backend development

The backend uses Django 5.2 LTS, Django Ninja, Uvicorn, and SQLite. The native Swift client uses the authenticated HTTP API for preferences, imports, source curation, job progress, grouping, domains, and wiki health.

## Start it

To start the server with its configured defaults (vault `~/second-brain`, server at `127.0.0.1:8000`) run

```bash
uv sync
make backend
```

To run the backend and macOS app together:

```bash
make dev
```

This builds a debug **Second Brain Dev** app and reuses an authenticated backend at the configured address, or starts one. On exit, it stops only the processes it started.

Both commands load the repository-root `.env`. For a separate development vault and port, with interactive API documentation, place the following in `.env`:

```dotenv
SECOND_BRAIN_VAULT=~/second-brain-scratch
SECOND_BRAIN_PORT=8001
SECOND_BRAIN_DEBUG=true
```

With these settings, Swagger docs are available at `http://127.0.0.1:8001/v1/docs`.

The installed app (acquired via `install.sh`) uses `~/second-brain` and port `8000` unless variables passed when launching it override them--it does not read the checkout's `.env`. It reuses an available backend or starts one, stopping only its own backend when the app quits. A backend started separately with `make backend` or `make dev` uses the settings from that launch.

Note: `bundle.sh` (called in `install.sh`) records the path to the checkout's `run.sh` in the app. The installed app therefore still runs backend code from that checkout. Use a separate checkout if backend code changes must not affect the installed app.

For development, restart the backend after changing worker code. Uvicorn's reload switch reloads the HTTP process but does not reload already-running processes.

## Code layout

| App | Responsibility |
|---|---|
| `library` | Source records, source reads/deletion, manual triage, library state |
| `ingestion` | Managed input copies, parsing, automatic chat triage |
| `compilation` | Manual builds, grouping plans, compilation agent, version markers |
| `wiki` | Content, domains, health, search, history, repairs |
| `jobs` | Job records, queueing, cancellation, job execution |
| `inference` | Local model files, inference workers, generation and embedding server /requests |
| `preferences` | Settings persistence, catalog, Keychain availability |
| `mcp` | MCP tool registration, stdio server, assistant connection setup |

Views translate HTTP into service calls and return results. `schemas.py` defines request and response fields; `models.py` and `migrations/` define database records. Backend startup in `django_config/server.py` starts `process_supervisor` to manage child processes and `file_watching` to run the ingestion and wiki watchers.

## Verification

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

## Tests

- `make test`: Python tests, with a disposable Django database and temporary vaults.
- `make gui-test`: Swift HTTP transport, state, and presentation tests. Requires full Xcode.
- `make gui-smoke`: creates a temporary vault, starts real workers/API, and runs the Swift client through settings, import, job progress, a dry-run build, and source deletion. It also verifies pushed updates after external vault writes and fresh state on reconnect. This command requires full Xcode, makes no paid model requests and removes the temporary vault afterward.

The root `conftest.py` contains fixtures shared across apps: vault configuration, authenticated in-process Django API client, job factory, worker progress binding, fake provider credentials, and Git isolation. Autouse guards block unmocked HTTPX requests and real Keychain reads in the pytest process. Subprocess integration tests use their own disposable environment and only exercise flows without provider calls. Factories are small callables with explicit defaults. Feature-specific setup belongs in the feature's `tests/conftest.py`. 

Swift tests share `Support/APITestHarness.swift`, with a unique vault/token/session per test, mock HTTP routing, and response factories.
