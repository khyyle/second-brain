# Contributing to second-brain

## First-time setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency and environment management. Install dependencies into the project virtual environment (`.venv`):

```bash
uv sync
```

That creates `.venv` and installs the dev tooling (`pytest`, `ruff`, `mypy`, `pre-commit`) alongside the runtime dependencies. Run any command inside the environment with `uv run` (e.g. `uv run pytest`), or use the `make` targets below.

Then install the git hooks once per clone, so linting and commit-message checks run automatically:

```bash
make install-hooks
```

## Daily workflow

Run these from the repo root:

| Command | What it does |
|---|---|
| `make test` | Run the full test suite (`uv run pytest`) |
| `make lint` | Run the `ruff` linter |
| `make format` | Auto-fix formatting and lint issues |
| `make typecheck` | Run `mypy` over `src` (opt-in) |
| `make check` | Lint + tests combined |
| `make sync` | Install/refresh dependencies into the uv env |
| `make install-hooks` | Install the pre-commit and commit-msg git hooks |

`mypy` is kept out of `make check` on purpose: the strict settings in `pyproject.toml` are not yet satisfied repo-wide, so it runs as a separate opt-in target until the type annotations are clean.

## Conventional Commits

All commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) spec. This is enforced by a `commit-msg` hook installed via `make install-hooks`.

### Format

```
<type>(<optional scope>): <description>

<optional body>

<optional footer>
```

### Types

| Type | When to use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Formatting, no logic change |
| `refactor` | Code restructure, no behavior change |
| `test` | Adding or updating tests |
| `chore` | Maintenance, dependency bumps |
| `build` | Build system or tooling changes |
| `ci` | CI/CD pipeline changes |
| `perf` | Performance improvements |
| `revert` | Reverting a previous commit |

### Scopes

Use a scope to clarify which part of the codebase is affected:

`ingestion` `triage` `parsing` `compilation` `clustering` `mcp` `cli` `config` `gui` `docs` `ci`

### Examples

```
feat(mcp): add semantic search over wiki pages
fix(mcp): keep the server alive by running it via __main__
perf(mcp): re-embed only changed pages on sync
test(compilation): mock the API key guard in the cost-cap test
chore: bump ruff to the latest release
docs(contributing): document the uv-based workflow
```

## Pull Requests

### Title

PR titles must follow the same Conventional Commits format as commit messages:

```
<type>(<optional scope>): <description>
```

This matters because squash merges use the PR title as the commit message on the target branch.

### Body

Individual commits already carry granular detail (type, scope, description).
The PR body should provide the higher-level picture:

```markdown
## What
<what this PR accomplishes as a whole>

## Why
<motivation, context, or problem being solved>

## Test Plan
- [ ] how to verify the change works

Closes #<issue>
```

Include a **Breaking Changes** section if applicable:

```markdown
## Breaking Changes
- what breaks and how to migrate
```

### Guidelines

- Keep PRs focused — one logical change per PR.
- Run `make check` and ensure the git hooks pass before opening a PR.

## Branch naming

```
<type>/<short-description>
```

Examples:

```
feat/semantic-search
fix/mcp-startup
perf/incremental-index
chore/repo-hygiene
```
