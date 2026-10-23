# Backend development

The Django backend starts the migration toward a local HTTP API for the Swift app.
It uses Django 5.2 LTS, Django Ninja, Uvicorn, and SQLite in the existing Python
package and uv environment. Development runs directly on macOS; Docker is not required.

The scaffold currently exposes a typed health endpoint and development API docs.
Feature apps are registered but contain no models or application endpoints yet.
The existing CLI, ingestion, compilation, and Swift integrations continue to run
through their existing code. The launcher prepares a development vault, applies
Django migrations, and starts the API. It does not start watchers or processing
workers. Runtime settings create a private signing identity on first use when no
key override is supplied; database creation and migrations belong to the launcher.

## Run the API

From the repository root:

```bash
uv sync --locked
make backend
```

The server listens on `127.0.0.1:8000` and uses `~/second-brain`. Debug, interactive
API docs, and code reload are disabled by default. No environment variables or
`.env` file are required. Stop it with Ctrl-C.

For development, set `SECOND_BRAIN_DEBUG=true` in `.env`. Optionally set
`SECOND_BRAIN_VAULT=~/second-brain-dev` if you want disposable data; a development
vault is an explicit choice, not the application's default.

`make backend` runs `uv run python start.py`. The installed command
`uv run second-brain-backend` calls the same implementation. To disable automatic
code reloading for a run, use `uv run python start.py --no-reload`.

Startup runs Django's system checks, creates the selected vault directory, rejects
legacy pipeline databases, and applies migrations before opening the HTTP listener.
A failed migration stops startup. Migrations run once per launch, not each time
Uvicorn reloads the ASGI application. Restart after generating a new migration.

| URL | Purpose |
| --- | --- |
| `http://127.0.0.1:8000/v1/health` | Returns `{"status":"ok"}` when the API process responds. It does not check worker or database readiness. |
| `http://127.0.0.1:8000/v1/docs` | Interactive Ninja API documentation when debug is enabled. |
| `http://127.0.0.1:8000/v1/openapi.json` | Machine-readable API contract when debug is enabled. |

The only application endpoint is the health check. Authentication for application
operations must be wired in before exposing vault content or mutations. Django's
`SECRET_KEY` is a signing secret, separate from the local API access token planned
for the Swift client.

## Project layout

```text
manage.py
start.py
.env.example
pyproject.toml
uv.lock
src/second_brain/
    django_config/
        settings/
            base.py
            runtime.py
            test.py
        environment.py
        server.py
        api.py
        urls.py
        asgi.py
        tests/
    apps/
        library/
        ingestion/
        compilation/
        wiki/
        jobs/
        preferences/
    llm/
    ollama/
    ...existing pipeline and CLI modules
```

`django_config` assembles the project. Each feature app owns its models, migrations,
schemas, views, services, and tests. All apps share one Django project and database.
The package stays under `src/second_brain` so the existing wheel includes it and the
CLI, API, and future workers share an installation.

`manage.py` and `start.py` sit beside `pyproject.toml` so commands are easy to run.
The actual server implementation lives in the package and is included in the wheel.
`src` is the Python package layout; it does not mean this code is separate from the
backend. There is one Python project and dependency environment.

| App | Intended ownership |
| --- | --- |
| `library` | Source identities, provenance, triage decisions, source queries and actions. |
| `ingestion` | Import records, capture, watched-folder intake, parsing, OCR, and publication of parsed sources. |
| `compilation` | Build requests, frozen inputs, grouping plans, and compilation. |
| `wiki` | Wiki content operations, domains, history, and repair. |
| `jobs` | Execution status, progress, attempts, cancellation, and recovery bookkeeping. |
| `preferences` | User preferences and application catalog operations. |

New feature code should live in its owning app. Existing pipeline code stays in its
current package until that feature is migrated; avoid copying it into two locations.
Shared provider clients such as `llm` and `ollama` remain ordinary Python packages.

## Services and processing packages

Each app starts with a `services/` package. Add modules named for meaningful operations,
such as `compilation/services/request_build.py`. Services coordinate validation,
state transitions, and transaction boundaries. They accept IDs and ordinary Python
arguments, so views, worker entry points, and management commands can call them.

Keep views concerned with HTTP and schemas concerned with request/response data.
An ORM model is a persistence definition; an API schema specifies fields a caller
may send or receive.

As compilation moves into its app, its structure can grow to:

```text
compilation/
    apps.py
    models.py
    migrations/
    schemas.py
    views.py
    services/
        request_build.py
        cancel_build.py
    tasks.py
    compiler.py
    agent/
    clustering/
    tests/
        test_views.py
        test_services.py
        test_agent/
        test_clustering/
```

`agent` and `clustering` are substantial processing packages. Keeping them beside
`services` lets that folder describe the application's operations without absorbing
every algorithm. They still belong to the compilation app, with all tests at the app
root. `tasks.py` will hold worker entry points once execution is implemented; it is
not needed for the health-only scaffold.

Keep database transactions short. A service may commit a build request and its job
together; parsing and model calls run outside that transaction. A worker records
results in subsequent short transactions. Database transactions cannot roll back
filesystem changes or provider requests.

## Register an endpoint

Every app has a Ninja `router` in `views.py`. The project mounts these routers under
`/v1`; each app supplies its complete resource path. For example, a library view
registered with `@router.get("/sources", response=...)` becomes `/v1/sources`.

This permits an app to own several related resources, such as compilation owning
both `/builds` and `/grouping-plans`. Add operations to the app router rather than
importing the project-wide `api` instance into feature modules.

## Settings and storage

| Module | Purpose | Behavior |
| --- | --- | --- |
| `settings/base.py` | Shared framework definitions | Installed apps, middleware, URLs, UTC timestamps, and SQLite connection options. Not a launch profile. |
| `settings/runtime.py` | The single runtime settings module | Used by the server, management commands, and ASGI entry point for every vault. Reads startup options. |
| `settings/test.py` | Isolated test configuration | Selected by pytest. Uses an in-memory database without loading `.env` or runtime settings. |

Changing vaults does not change `DJANGO_SETTINGS_MODULE`. Set `SECOND_BRAIN_VAULT`
in the repository's `.env` and both the server and management commands use it.
Paths must be absolute; `~` is expanded. For example:

```dotenv
SECOND_BRAIN_VAULT=~/second-brain
SECOND_BRAIN_PORT=8000
```

`.env.example` documents the supported startup options. Copy it only if you do not
already have a `.env` file; otherwise add the entries you want to your existing file.
The loader reads only these four options:

| Startup option | Default | Meaning |
| --- | --- | --- |
| `SECOND_BRAIN_VAULT` | `~/second-brain` | Directory containing this backend's `manifest.db` and future application configuration. |
| `SECOND_BRAIN_PORT` | `8000` | Local HTTP port. `0` requests an available port, reported by Uvicorn. |
| `SECOND_BRAIN_DEBUG` | `false` | Development tracebacks and API docs. Also supplies the default for code reload. |
| `SECOND_BRAIN_SECRET_KEY` | Generated and persisted automatically | Optional override for Django's signing key, separate from provider keys and API authentication. |

Process environment values override `.env` values. The loader finds `.env` relative
to the source checkout, never by searching the shell's current directory or parents.
An installed wheel does not search for a checkout's `.env`. A launcher may supply
an explicit `SECOND_BRAIN_ENV_FILE` path; an empty value disables file loading.
Restart the process after changing startup options. There is no separate reload
environment variable: `start.py --reload` and `--no-reload` override that launch behavior.

Without an override, Django's signing key is generated once and persisted in
`<vault>/.backend/django-secret-key` with owner-only permissions. Startup and
management commands share that key. Concurrent first launches converge on the same
key; restarting never rotates it. App installation, service supervision, and Swift
connection discovery are still to be implemented.

Django settings control framework behavior. User-editable preferences such as the
selected model and spend cap belong to the preferences feature and its vault storage.
They should not be added as mutable Django settings or environment variables.
The intended store is `<vault>/config.yaml`, read and written through validated
preferences services. That store and its HTTP operations are still to be implemented;
the existing pipeline continues to use its existing configuration loader.

### Provider credentials and local HTTP authentication

The target is for the GUI to manage provider credentials using macOS Keychain APIs.
Background processing must be able to retrieve the corresponding credentials through
an authorized backend credential service or native helper, including after the GUI
closes. Sharing Keychain access requires appropriate access controls and, for the
Data Protection Keychain, signing and access-group configuration. Sharing is not
automatic just because the programs run as the same user.

Provider secrets should not be attached to every GUI request, saved in job payloads,
or stored in the vault configuration file. Requests that start jobs identify the
provider; workers resolve its credential when executing. The local HTTP API will
use a separate access token. A provider key and Django's signing key are not that token.
Loopback binding limits network exposure but does not authenticate a client or encrypt
HTTP headers. The token boundary must be implemented before data or credential
operations are exposed, and credentials must not appear in request logs.

Keychain integration and local API authentication are not implemented in this scaffold.
The only application operation is the non-sensitive health check. The new backend
`.env` loader ignores provider-key entries; the existing GUI and CLI still use the
legacy `.env` credential flow until their migration is implemented.

SQLite connections use WAL, a five-second busy timeout, and `IMMEDIATE` transactions
for explicit atomic blocks. Each block should be short; requests are not automatically
wrapped in transactions. SQLite still permits one writer at a time and does not
provide `SELECT FOR UPDATE` row locks. Transaction and recovery behavior needs its
own tests when model operations are implemented.

## Migrations

Use a new development vault for the first Django models. For example:

```bash
uv run python manage.py makemigrations library
make backend
```

There are no feature migrations yet, so `makemigrations` currently reports no changes.
Commit generated migration files with the corresponding model changes.
For a vault already initialized by the launcher, `uv run python manage.py migrate`
also works as a normal Django management command.

The existing production `manifest.db` uses the legacy schema and migration runner.
Adopting those tables requires a deliberate migration and data-preservation step.
The launcher refuses a database containing the legacy `manifest` table. Pointing
`manage.py migrate` at that file bypasses this launcher check and does not perform
the conversion. Use a new development vault until adoption has been implemented.

## Tests and checks

```bash
make backend-test
make backend-check
make lint
make test
```

New feature tests live in `apps/<feature>/tests`. Project assembly and settings tests
live in `django_config/tests`. The root pytest configuration discovers both locations
and the existing `tests/` suite. Use `pytest.mark.django_db` for tests that need the
ORM; ordinary service and algorithm tests should request database access only when
they use it.

The scaffold tests exercise HTTP and ASGI health requests, the generated contract,
host validation, dotenv precedence, settings isolation, successful and failed startup
migrations, repeated startup, persistent signing identity, and preservation of legacy
databases. With an explicit signing key, settings and ASGI imports do not touch
storage or load processing code.

## Next implementation slices

1. Add the local API authentication boundary, then catalog and settings operations
   in `preferences`, with explicit schemas and validation tests.
2. Define the first source models and migrations in `library` against a development
   database; implement source listing before integrating existing records.
3. Design the legacy database adoption and verify it on copied data.
4. Add durable jobs and worker entry points, then connect ingestion and compilation
   one operation at a time.
5. Move Swift callers onto the corresponding endpoints as each operation is ready.

Start watchers and workers through explicit process entry points. Do not start them
in module imports or `AppConfig.ready()`, which also runs during checks, tests, and
management commands. Development reload must not create extra pipeline owners.

## References

- [Django projects and applications](https://docs.djangoproject.com/en/5.2/ref/applications/)
- [Django Ninja routers](https://django-ninja.dev/guide/routers/)
- [Django SQLite behavior](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes)
- [pytest-django configuration](https://pytest-django.readthedocs.io/en/latest/configuring_django.html)
- [Original web scaffold structure](https://github.com/khyyle/django-react-scaffold)
- [Uvicorn startup and reload options](https://uvicorn.dev/settings/)
- [python-dotenv configuration parsing](https://bbc2.github.io/python-dotenv/)
- [Apple Keychain sharing](https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps)
