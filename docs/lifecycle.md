# Data lifecycle and deletion

This document explains what Second Brain records about a source, how hashing and caching avoid repeated work, what each kind of delete removes, and which files are safe to edit by hand. For the high-level pipeline, see [the architecture document](architecture.md).

## What gets recorded

A single SQLite database, `~/second-brain/manifest.db`, records everything the pipeline knows about a source: its content hash and ingestion status, the OCR cache for rendered PDF pages, each chat's triage verdict, and which sources have been compiled into the wiki. For table-level detail see [wiki structure](wiki-structure.md#the-two-databases).

The parsed Markdown lives under `~/second-brain/raw/`, the wiki under `~/second-brain/wiki/` (a git repository), and the keyword/embedding index in `~/second-brain/search.db`.

One piece of build state stays out of the database on purpose. When you preview a clustering, the proposed grouping is written as two JSON files in the vault root: `.clusters.json` (the groups and their estimated cost) and `.cluster-overrides.json` (any group you split or chat you popped out). Both are disposable--a finished build clears them, and re-previewing rewrites them, so they hold an intention for the next build rather than permanent state.

## The life of a source

1. A file is dropped or copied into `drops/`. Ingestion picks it up and records a manifest row.
2. The parser writes Markdown into `raw/<lane>/`, the manifest row is marked complete with the source's content hash, and the original is removed from `drops/`.
3. If the source is in a triaged lane (ChatGPT by default), triage records a verdict and a review-tier chat is copied into `inbox/`. A document you dropped skips this step and carries no verdict.
4. On build, the agent writes a wiki page, the source is marked compiled, and the wiki commit is made. Re-ingesting the same source later clears its compiled marker so the next build refreshes it.

## How the wiki is versioned

The wiki directory is its own git repository. The first build runs `git init` if needed, and the pipeline manages the history from there.

A build creates checkpoints as it makes progress. When the compilation agent finishes a source or a reviewed cluster of sources, that wiki change is committed immediately. If a later source fails, or if you stop the build, only the unfinished work is discarded. After the last source is compiled, the deterministic structure rebuild runs and commits the generated index, gap list, domain views, and recently-updated list once more. Domain edits (a rename, merge, or delete, from the app or the CLI) are checkpointed the same way, each as its own commit naming the change.

Each checkpoint stages the whole wiki tree and is skipped when nothing changed. This has one consequence worth knowing: hand edits left uncommitted in the wiki are swept into the next build's checkpoint. They are preserved, not lost, but they will sit under an automatic commit message instead of one of your own. Commit by hand first if you want those edits separated. Overall, the wiki history is a local chain of checkpoints that can be inspected and rolled back with the [`wiki` commands](cli.md#wiki-maintenance).

## Hashing and caching

**Content hashing** dedupes whole sources. Every file is hashed by its bytes, so an identical file is skipped rather than parsed again, even under a different name. Separately, the assembled Markdown is hashed so a re-export whose text is unchanged can short-circuit downstream work.

**Page caching** dedupes OCR. Each PDF page is rendered to an image and hashed, and the OCR result is stored under that hash. Re-exporting a notebook after editing one page changes only that page's image, so only that page is re-OCR'd; every other page is served from the cache. A page that fails to parse is never cached, so a retry re-runs it cleanly.

Both caches are disposable. The page cache can always be rebuilt by re-OCR, and `search.db` is rebuilt from the wiki whenever the MCP server starts. Deleting either is safe; deleting `manifest.db` is also safe but forces everything to be re-ingested.

## Deletion

Removal happens in two places: hovering a row in the app reveals its remove action, and the CLI's `forget-drop` and `forget`. What a removal means depends on how far the source has traveled:

- **Remove a file that is still ingesting** (an Ingest row, or `second-brain forget-drop <path>`). The file in `drops/` is trashed and nothing remembers it was ever added.
- **Skip a chat** (Skip on a Chats row). The conversation leaves the build corpus but its verdict is remembered, so re-importing the same export keeps it skipped instead of resurfacing it for review. A skip is recoverable until the next build: **Keep** on the skipped row re-stages it, while a completed build makes the removal permanent (the verdict stays).
- **Remove a staged source** (a Build row, or `second-brain forget <raw-path>`). This forgets the source entirely: its Markdown in `raw/` is trashed and every record of it is cleared with it, so a re-import would treat it as new.

Skipping and removing both take a source out of the build. The difference is memory: a skip keeps the verdict and can be undone, while removal forgets immediately. Either way the related records are cleared together, so a removed source never leaves a dangling compiled marker behind.

## Self-healing

The manifest does not blindly trust its own "complete" status. Before skipping a source it checks that the raw Markdown still exists on disk, so deleting files under `raw/` in Finder makes the next run re-ingest them rather than silently treating them as done. This keeps a hand-pruned `raw/` tree and the manifest from drifting apart.

Triage and clustering are forgiving in the same spirit. If a source disappears mid-run, perhaps because you un-ingest it from the app while work is in progress, that source is skipped rather than treated as fatal. Editing the vault during a run slows it down at worst instead of crashing it.

## What is safe to edit by hand

- **`wiki/*.md`** is yours (or an agent's) to read, edit, and reorganize. Edits stay scoped to the compiled pages, never `raw/` or the manifest, and committing your own changes is fine. Avoid history surgery by hand: undoing a build or recovering needs the manifest and links to stay in sync, so prefer using the `wiki` commands (see [command line](cli.md#wiki-maintenance)).
- **`config/config.yaml`** — the pipeline settings. The Settings panel edits a subset of these in place, leaving comments and ordering intact.
- **`sources.json`** — the list of watched folders, also managed by the Watched folders control in Settings.

Avoid hand-editing these:

- **`manifest.db` and `search.db`** — use the CLI deletes or the menu bar app instead of editing rows directly as the index rebuilds itself.
- **`wiki/_meta/` and `wiki/_views/`** — these are regenerated on every build, so manual changes are overwritten.
