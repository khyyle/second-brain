# Data lifecycle and deletion

This document explains what Second Brain records about a source, how hashing and caching avoid repeated work, what each kind of delete removes, and which files are safe to edit by hand. For the high-level pipeline, see [the architecture document](architecture.md).

## What gets recorded

A single SQLite database, `~/second-brain/manifest.db`, holds four tables:

- **manifest** — one row per source file: its content hash, ingestion status, which parser lane handled it, and the path of the Markdown it produced.
- **page_cache** — OCR output for a single rendered PDF page, keyed by the hash of the page image.
- **triage** — the worthwhile / review / skip verdict for each triaged source. Only chats are triaged; a document you dropped has no row here.
- **compiled** — which raw sources have been folded into the wiki.

The parsed Markdown lives under `~/second-brain/raw/`, the wiki under `~/second-brain/wiki/` (a git repository), and the keyword/embedding index in `~/second-brain/search.db`.

One piece of build state stays out of the database on purpose. When you preview a clustering, the proposed grouping is written as two JSON files in the vault root: `.clusters.json` (the groups and their estimated cost) and `.cluster-overrides.json` (any group you split or chat you popped out). Both are disposable — a finished build clears them and re-previewing rewrites them — so they hold an intention for the next build, never anything permanent.

## The life of a source

1. A file is dropped or copied into `drops/`. Ingestion picks it up and records a manifest row.
2. The parser writes Markdown into `raw/<lane>/`, the manifest row is marked complete with the source's content hash, and the original is removed from `drops/`.
3. If the source is in a triaged lane (ChatGPT by default), triage records a verdict and a review-tier chat is copied into `inbox/`. A document you dropped skips this step and carries no verdict.
4. On build, the agent writes a wiki page, the source is marked compiled, and the wiki commit is made. Re-ingesting the same source later clears its compiled marker so the next build refreshes it.

## Hashing and caching

**Content hashing** dedupes whole sources. Every file is hashed by its bytes; if an identical file has already been ingested — even under a different name — it is skipped rather than parsed again. Separately, the assembled Markdown is hashed so a re-export whose text is unchanged can short-circuit downstream work.

**Page caching** dedupes OCR. Each PDF page is rendered to an image and hashed, and the OCR result is stored under that hash. Re-exporting a notebook after editing one page changes only that page's image, so only that page is re-OCR'd; every other page is served from the cache. A page that fails to parse is never cached, so a retry re-runs it cleanly.

Both caches are disposable. The page cache can always be rebuilt by re-OCR, and `search.db` is rebuilt from the wiki whenever the MCP server starts. Deleting either is safe; deleting `manifest.db` is also safe but forces everything to be re-ingested.

## Deletion

There are four distinct removals, because "remove" has varying meaning at different stages:

- **Drop a queued or failed item** removes a file that has not finished ingesting. It trashes the file in `drops/` and clears its manifest row (CLI: `second-brain forget-drop <path>`).
- **Skip a source** marks it not worthwhile. It moves the Markdown out of the build corpus into a hidden `raw/.skipped/` holding folder and records the `skip` verdict, so the file leaves `raw/` while the decision is remembered — it still shows in the Chats tab as skipped, and re-importing the same export keeps it skipped instead of resurfacing it for review. A skip is recoverable: **Keep** on the skipped row moves it back and re-stages it. A completed build then permanently clears `raw/.skipped/` (the verdicts stay), so nothing lingers past a build; a stopped build leaves it intact.
- **Un-ingest a staged source** forgets it entirely. It trashes the Markdown in `raw/` and clears the source's manifest, compiled, and triage rows together, so nothing remembers it; a re-import would treat it as new (CLI: `second-brain forget <raw-path>`).
- **Stop a build** discards only the in-progress document. Completed sources are committed as the build goes, so a stop rolls back the uncommitted page for the current source and leaves the rest staged for next time.

Skip and un-ingest both take the source out of the build; the difference is memory and recoverability — skip keeps the verdict and can be undone until the next build, un-ingest forgets immediately. Deletes that touch the database clear related rows together, so a removed source never leaves a dangling compiled marker behind.

## Self-healing

The manifest does not blindly trust its own "complete" status. Before skipping a source it checks that the raw Markdown still exists on disk, so deleting files under `raw/` in Finder makes the next run re-ingest them rather than silently treating them as done. This keeps a hand-pruned `raw/` tree and the manifest from drifting apart.

Triage and clustering are forgiving in the same spirit. A source that disappears mid-run — say you un-ingest it from the app while a run is working — is skipped rather than fatal, so editing the vault during a run slows it down at worst instead of crashing it.

## What is safe to edit by hand

- **`wiki/*.md`** — yours (or an agent's) to read, edit, and reorganize. The wiki is its own git repository, separate from `raw/` and the manifest, so edits and rollbacks are safe and scoped to the compiled pages. A rollback restores the pages, but `manifest.db` still treats their sources as compiled, so a normal build won't redo them--run `second-brain compile --full` to recompile every staged source from scratch.
- **`config/config.yaml`** — the pipeline settings. The Settings panel edits a subset of these in place, leaving comments and ordering intact.
- **`sources.json`** — the list of watched folders, also managed by the Watched folders control in Settings.

Avoid hand-editing these:

- **`manifest.db` and `search.db`** — use the CLI deletes or the menu bar app instead of editing rows directly as the index rebuilds itself.
- **`wiki/_meta/` and `wiki/_views/`** — these are regenerated on every build, so manual changes are overwritten.
