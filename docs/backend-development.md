# Backend development

This is a runnable Django scaffold for implementing the backend yourself. It uses
Django 5.2 LTS, Django Ninja, Uvicorn, and SQLite. Docker is unnecessary.

**Implemented:** startup, settings, local API authentication, ORM models, initial
migrations, and the health endpoint. Existing processing code and its tests have
moved into feature apps. Imports in the CLI, MCP server, and installer follow them.

**Scaffolded:** 42 application endpoints with named handler functions, request and
response schemas, and TODOs identifying the code to connect. Valid authenticated
requests return `501 Not Implemented`. Invalid inputs return `422`; missing or wrong
API credentials return `401`. No stub creates jobs, changes files, or runs a model.

**Still yours to implement:** endpoint bodies, ORM query/write services, vault
preferences storage, worker execution, Keychain access, and Swift integration. The
existing CLI and Swift app retain their behavior while you migrate one feature at
a time. The new `Job` table does not yet track legacy CLI runs.

## Start and explore

```bash
uv sync --locked
make backend
```

This uses **`~/second-brain`**, listens on `127.0.0.1:8000`, and disables debug and
reload by default. No `.env` is required. Stop with Ctrl-C.

For interactive development, add this to your existing repository `.env`:

```dotenv
SECOND_BRAIN_DEBUG=true
```

Then restart `make backend` and open <http://127.0.0.1:8000/v1/docs>. The default
vault remains `~/second-brain`. You may explicitly select disposable data with
`SECOND_BRAIN_VAULT=~/second-brain-dev`; that is an override, not another settings
profile. Existing `.env` entries for the old CLI are not imported by the backend.

The docs' **Authorize** dialog accepts the token in
`<vault>/.backend/api-token`. Paste its contents without a `Bearer` prefix. This is
a private local API credential, separate from Django's signing key and provider
API keys. The launcher creates it once with owner-only permissions. Do not commit
or share it. `/v1/health` is public and returns `{"status":"ok"}`.

After authorization, try `GET /sources` to see its `501`, then try `limit=0` to see
Ninja's validation error. `/v1/openapi.json` describes all request/response shapes.
Docs and schema URLs are available only when debug is enabled. Application routes
remain authenticated in debug mode.

`make backend` runs `uv run python start.py`. The installed
`uv run second-brain-backend` command calls the same launcher. Use
`uv run python start.py --no-reload` to disable reload for one run. `start.py` runs
checks and migrations before Uvicorn; the ASGI reload child does not repeat them.
Restart after adding a migration. No watchers or processing workers are started.

## Structure and responsibilities

```text
manage.py                       Django management commands
start.py                        Thin backend launcher
conftest.py                     Fixtures shared by all Python tests
src/second_brain/
    django_config/              Settings, startup, authentication, router assembly
    apps/
        library/                Source identities and triage records
        ingestion/              Imports, parsing, OCR, triage execution
        compilation/            Build execution and optional chat grouping
        wiki/                   Markdown pages, domains, history, repair
        jobs/                   Job records and existing progress/state helpers
        preferences/            Settings, catalog, credentials, integrations
    config.py                   Shared validated configuration types and legacy loader
    db.py                       Legacy SQLite adapter, retained during ORM migration
    llm/                        Shared provider clients and catalog
    ollama/                     Shared local model access
    search/                     Shared search primitives
    mcp_server/                 MCP transport and integration helpers
    scheduler/                  Existing launchd integration
    cli.py                      Existing command adapter and orchestration
```

A feature app has this shape:

```text
compilation/
    apps.py
    models.py
    migrations/
    schemas.py
    views.py
    services/
        compiler.py
        store.py
        agent/
        clustering/
    tests/
        test_compiler.py
        test_store.py
        test_agent/
        test_clustering/
```

`services/` contains the existing implementation, including `agent/` and
`clustering/`. Tests sit at the app root and follow that structure. Shared
infrastructure stays outside the apps because several features use it. There are
no duplicate copies of the old processing packages.

The intended flow is:

```text
HTTP request -> view + request schema -> service -> models/files/providers
HTTP response <- response schema     <- service result
```

- **Views** handle HTTP inputs, status codes, and calls into services.
- **Schemas** define what clients can send and receive. `ModelSchema` is used where
  an explicit selection of model fields fits, such as `SourceOut` and `JobOut`.
  Request schemas are separate so callers cannot write arbitrary model fields.
- **Services** implement operations, validation, and transaction boundaries. They
  should accept ordinary arguments, not Django requests, so CLI and workers can
  call them too.
- **Models** describe persisted records. They do not start workers or make model
  API calls.

All routers are registered under `/v1`. Each app supplies complete resource paths,
for example `library/views.py` registers `/sources` as `/v1/sources`.

## Models and state ownership

| App | Models | Storage |
| --- | --- | --- |
| Library | `Source`, `TriageDecision` | Existing `manifest` and `triage` tables |
| Ingestion | `PageCache` | Existing `page_cache` table |
| Compilation | `CompiledSource`, `DeferredSource` | Existing `compiled` and `deferred_sources` tables |
| Wiki | `DismissedDuplicate` | Existing `dismissed_duplicates` table |
| Jobs | `Job` | New `jobs_job` table |
| Preferences | None | Intended validated `<vault>/config.yaml`; API persistence remains to implement |

Source keys remain existing file/raw paths so adopting Django does not change
source identity or invalidate pipeline records. `DismissedDuplicate` retains its
pair of stems as a composite primary key. A `Job` has a UUID, kind, execution lane,
status, timestamps, cancellation request, input/config snapshots, result, and an
optional `retry_of` link. Its public schema excludes internal payload/config fields.
Never put credentials in snapshots or results.

Wiki content remains Markdown, with history in Git. Original/captured files,
parsed Markdown, and build artifacts stay in the vault. The catalog remains
application definitions in Python. Provider credentials belong in Keychain; they
do not need a Django model. Search data remains a rebuildable index.

The legacy stores now live in app services but still use SQLite directly. Changing
imports did not convert those methods into ORM queries. Their replacement is part
of the implementation work: keep the algorithms and replace persistence calls as
you implement each feature. `db.py` and existing stores must remain until their CLI,
worker, and MCP callers have migrated.

### Database adoption

The launcher supports existing schema versions 1–3. Before the first adoption it
uses SQLite's backup API, including committed WAL contents, to save an owner-only
copy under `<vault>/.backend/backups/`. Unknown versions stop startup.

The library initial migration creates or upgrades the six existing tables and
validates required columns. `SeparateDatabaseAndState` registers their Django
model definitions without recreating populated tables. Other feature migrations
depend on that adoption; the jobs migration creates the new job table normally.
The legacy `user_version` remains 3 so current CLI stores continue working.

These initial migrations intentionally retain old schema details for compatibility.
After adoption, add schema changes through Django migrations:

```bash
uv run python manage.py makemigrations library
make backend
```

Commit models and migrations together. The initial adoption is intentionally
irreversible through `migrate ... zero`; restoring the pre-adoption backup is a
separate, explicit operation. `manage.py migrate` applies the migrations directly
but does **not** run the launcher's backup step. Use `make backend` for first
adoption of an existing vault, with the old app/pipeline stopped.

SQLite uses WAL, a five-second busy timeout, and `IMMEDIATE` transactions for
explicit `transaction.atomic()` blocks. SQLite has one writer and does not provide
row locking through `select_for_update()`. Keep transactions short. For example,
claim a queued job with a conditional `filter(status="queued").update(...)` and
check the affected row count. Parsing, provider calls, and filesystem operations
belong outside DB transactions; a database rollback cannot undo them.

## Endpoint implementation map

All paths below are relative to `/v1`. Each handler's TODO gives more context.
These are proposed contracts you can refine as you implement them.

| App | Endpoints | Existing implementation to adapt |
| --- | --- | --- |
| Library | `GET /sources`, `GET /sources/detail` | `Source` ORM; Swift `ManifestReader` queries |
| Library | `GET /sources/content`, `DELETE /sources` | Raw files, `services.sources.forget_source`, Swift `ManifestMutator` |
| Library | `POST /sources/recompile` | `cli.recompile`, compilation store |
| Library | `GET /triage`, `PATCH /triage` | `TriageDecision`, `cli.triage_set`, skip/restore movement in Swift |
| Library | `GET /state` | `jobs.services.state.compute_state` |
| Ingestion | `POST /imports`, `DELETE /imports` | Swift `DropStaging`, `cli.ingest`, `cli.forget_drop` |
| Ingestion | `POST /ingestion/runs` | `services.watcher.batch_scan`, `dispatch`, `triage.pipeline` |
| Compilation | `POST /builds` | `cli.compile`, `services.compiler.run_compilation` |
| Compilation | `POST /grouping-plan/preview` | `cli.preview_clusters`, `services.clustering.preview` |
| Compilation | `GET /grouping-plan`, `PUT /grouping-plan/overrides`, `DELETE /grouping-plan` | Preview/override artifacts and Swift `ClusterPlan` |
| Jobs | `GET /jobs`, `GET /jobs/{job_id}` | New `Job` ORM plus existing progress helpers |
| Jobs | `POST /jobs/{job_id}/cancel`, `POST /jobs/{job_id}/retry` | New lifecycle service; current cooperative stop handling |
| Wiki | `GET /wiki/pages`, `GET /wiki/content`, `GET /wiki/search` | Wiki discovery, MCP page/search helpers, `cli.query` |
| Wiki | `GET /wiki/health` | `cli.health`, `services.health`, dismissed-duplicate records |
| Wiki | `GET /domains`, `POST /domains/rename`, `POST /domains/merge`, `DELETE /domains` | `services.domain_ops` |
| Wiki | `GET /wiki/history`, `POST /wiki/rollback` | `services.repo` and CLI history/rollback adapters |
| Wiki | `POST /wiki/repair-links`, `POST /wiki/merge`, `POST /wiki/dismissed-duplicates` | `services.repo`, `DismissedDuplicate` |
| Preferences | `GET /catalog` | `llm.SUPPORTED_MODELS`, provider profiles, configuration types |
| Preferences | `GET /settings`, `PATCH /settings` | Shared `Config` types; new vault configuration service |
| Preferences | `GET /watched-folders`, `PUT /watched-folders` | Current `sources.json`/`SourcesStore`; fold into vault settings |
| Preferences | `GET /credentials/{provider}` | New backend Keychain availability check; never return a key |
| Preferences | `GET /diagnostics` | `ollama.client.check_ollama`, `cli.doctor` |
| Preferences | `GET /integrations/mcp/{target}`, `PUT /integrations/mcp/{target}` | `mcp_server.install` helpers |

Build/import/preview/retry requests will return `202` with a job once implemented;
poll `/jobs/{job_id}` for progress. Recompilation stages selected sources and does
not itself launch a build. Documents go directly from ingestion to staging; chats
add triage and optional grouping. Builds remain manual.

Do not wrap `cli.py` in subprocesses from the views. Move its orchestration into
services and have both HTTP and CLI call those services. Native folder selection,
opening Finder, Keychain UI, and launching the backend belong to the native app.
The backend will own watchers; there is no endpoint to start another perpetual
watch loop or execute an arbitrary shell command. Automatic intake preferences
belong in settings. Existing launchd scheduling remains during migration.

## A useful implementation order

1. **List sources.** Start in `library/views.py:list_sources`. Query `Source`, apply
   the optional filter, order deterministically, count, and slice. Return the
   `SourcePage` shape and let Ninja serialize the selected model fields. This is a
   small ORM + API exercise with real persisted data.
2. **Read one source and list triage.** Implement missing-record handling and test
   the response schemas. These introduce lookups and filtering without file writes.
3. **Catalog and settings.** Catalog is a pure serialization exercise. Then add
   `preferences/services/configuration.py` to read/write `<vault>/config.yaml`,
   adopt existing preferences, and preserve omitted values during PATCH.
4. **Source/triage mutations.** Replace legacy SQL with short atomic operations,
   coordinate file changes, and test failure recovery and path containment.
5. **Jobs and execution.** Choose and wire task delivery, worker supervision,
   duplicate-claim protection, cancellation, and restart recovery before enabling
   POST handlers that promise execution. Huey's SQLite queue is a candidate; no
   worker library is installed by this scaffold. Celery/Redis is not required.
6. **Builds and wiki mutations.** Freeze build bytes, hashes, config, and grouping
   together. Serialize wiki writers while allowing ingestion separately. The old
   `.status.json` and `.stop` are global; make them job-specific before concurrency.
7. **Swift adapters.** Replace each SQL/file/shell caller when its endpoint works,
   then delete the superseded code and tests. Move credentials to native Keychain
   and explicitly establish backend access. The old GUI/CLI credential behavior
   has not changed yet.

When implementing a view, replace its `pending(...)` call and remove its `501`
response declaration. Add the actual error contracts it needs (`404`, `409`, etc.).
The scaffold helper is temporary; remove it when all operations are implemented.

## Startup settings

| Module | Responsibility |
| --- | --- |
| `settings/base.py` | Installed apps, middleware, UTC timestamps, SQLite options |
| `settings/runtime.py` | Single runtime module for every vault and normal launch |
| `settings/test.py` | Isolated pytest configuration with an in-memory database |

Only four startup values are read from the repository `.env`; process values win:

| Option | Default |
| --- | --- |
| `SECOND_BRAIN_VAULT` | `~/second-brain` |
| `SECOND_BRAIN_PORT` | `8000`; `0` chooses an available port reported by Uvicorn |
| `SECOND_BRAIN_DEBUG` | `false`; enables docs and defaults reload to on |
| `SECOND_BRAIN_SECRET_KEY` | Generated once in `<vault>/.backend/django-secret-key` |

No separate local/desktop profile is needed. `USE_TZ=True` keeps timestamps
consistent; `USE_I18N=False` disables unused Django translation machinery;
`APPEND_SLASH=False` avoids implicit redirects for API routes.

The loader locates `.env` from the checkout, not the working directory. An installed
wheel does not discover a checkout's file. `SECOND_BRAIN_ENV_FILE` can explicitly
select a file; an empty value disables loading. User preferences and provider keys
are not startup environment settings. App launch supervision and port discovery
for Swift are still implementation work.

## Validation

```bash
make backend-check
make backend-test
make lint
make test
uv run python manage.py makemigrations --check --dry-run
```

Feature tests live in each app; shared infrastructure/CLI tests remain in `tests/`.
Use `pytest.mark.django_db` when a test needs ORM access. Plain processing tests do
not need Django database fixtures. Shared fixtures live in the root `conftest.py`.

Migration tests adopt versions 1–3 in disposable databases, verify every existing
table's data, check ORM access, inspect backup contents, and repeat startup. API
tests check authentication, schemas, input validation, and explicit placeholder
responses. No test needs your real vault or provider credentials.
