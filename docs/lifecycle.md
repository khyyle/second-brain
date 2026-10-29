# Data lifecycle and deletion

This document explains what Second Brain records about a source, how hashing and caching avoid repeated work, what each kind of delete removes, and which files are safe to edit by hand. For the high-level pipeline, see [the architecture document](architecture.md).

## What gets recorded

A single SQLite database, `~/second-brain/manifest.db`, records everything the pipeline knows about a source: its content hash and ingestion status, the OCR cache for rendered PDF pages, each chat's triage verdict, which sources have been compiled into the wiki, and job progress/outcomes. For table-level detail see [wiki structure](wiki-structure.md#the-two-databases).

The parsed Markdown lives under `~/second-brain/raw/`, the wiki under `~/second-brain/wiki/` (a git repository), and the keyword/embedding index in `~/second-brain/search.db`.

When you preview a grouping, the proposed grouping is written as two JSON files in the vault root: `.clusters.json` (the groups and their estimated cost) and `.cluster-overrides.json` (any group you split or chat you popped out). A complete build clears them if sources and grouping have not changed since submission and ingestion is idle. Otherwise, they remain for review and a later build. Re-previewing replaces them.

## The life of a source

1. A file is dropped or copied into `drops/`. Watched originals are copied into the same queue, with their original path and captured content hash recorded in the manifest. All watched inputs are captured before parsing starts.
2. The parser writes Markdown into `raw/<source name>/`, the manifest row is marked complete with the source's content hash, and the queued copy is removed from `drops/`. Watched originals remain untouched. A failed copy stays in Ingest with its reason and Retry; automatic scans try it again only when its contents or the original change.
3. By default, ChatGPT conversations go through triage and other documents skip this step. Triage records a Keep, Review, or Skip decision; chats marked for review are copied into `inbox/`.
4. A build captures eligible Markdown and preferences when submitted. The agent writes wiki pages from those copies, records the compiled versions, and makes a wiki commit. A changed parsed version is eligible for a later build.

## How the wiki is versioned

The wiki directory is its own git repository. The first build runs `git init` if needed, and the pipeline manages the history from there.

A build creates checkpoints as it makes progress. When the compilation agent finishes a source or a reviewed cluster of sources, that wiki change is committed immediately. If a later source fails, or if you stop the build, only the unfinished work is discarded. When the build ends, the backend rebuilds and commits the index, gap list, domain views, and recently-updated list from the saved pages. Domain edits (a rename, merge, or delete, from the app or the CLI) are checkpointed the same way, each in a separate commit.

Each checkpoint stages the whole wiki tree and is skipped when nothing changed. This has one consequence worth knowing: hand edits left uncommitted in the wiki are swept into the next build's checkpoint. They are preserved, not lost, but they will sit under an automatic commit message instead of one of your own. Commit by hand first if you want those edits separated. Overall, the wiki history is a local chain of checkpoints that can be inspected and rolled back with the [wiki maintenance commands](cli.md#wiki-maintenance).

## Hashing and caching

**Content hashing** dedupes whole sources. Every file is hashed by its bytes, so an identical file is skipped rather than parsed again, even under a different name. Separately, the assembled Markdown is hashed so a re-export whose text is unchanged can short-circuit downstream work.

**Page caching** dedupes OCR. Each PDF page is rendered to an image and hashed, and the OCR result is stored under that hash. Re-exporting a notebook after editing one page changes only that page's image, so only that page is re-OCR'd; every other page is served from the cache. A page that fails to parse is never cached, so a retry re-runs it cleanly.

The page cache can be rebuilt by re-OCR, and the backend refreshes `search.db` from the wiki. Stop the backend before removing a damaged search index. Do not delete `manifest.db` to reset a cache: it also contains triage decisions, build records, and job history that cannot be reconstructed from the wiki.

## Deletion

Use a row's remove action in the app or the [source-management commands](cli.md#common-commands). What a removal means depends on how far the source has traveled:

- **Remove a pending input** (an Ingest row, or `second-brain forget-drop`). The file in `drops/` moves to the vault's `.trash/` and its record is removed. Wait for ingestion to finish, or cancel its queued jobs, before removing the copy. If its original remains in a watched folder, a later scan can capture it again.
- **Skip a chat** (Skip on a review row or a staged chat). The conversation leaves the staged list but its verdict is remembered, so re-importing the same export keeps it skipped instead of resurfacing it for review. A skip, yours or the triage model's, is recoverable until the next build: **Keep** on the skipped row re-stages it, while a complete build can purge the skipped copy (the verdict stays). Cleanup waits if sources or grouping changed after submission or ingestion is still active.
- **Remove a staged source** (a Build row, or `second-brain forget`). This forgets the source entirely: its Markdown in `raw/` moves to the vault's `.trash/` and every record of it is cleared with it, so a re-import would treat it as new. An original still in a watched folder can be captured again on a later scan.

Skipping and removing both take a source out of the build. The difference is memory: a skip keeps the verdict and can be undone, while removal forgets immediately. Removal also clears any record that the source was compiled or set aside.

Source deletion, triage changes, and restaging are blocked while ingestion is queued or running. They are also blocked for sources included in a queued or running build.

## Self-healing

The manifest does not blindly trust its own "complete" status. Before skipping a source it checks that the raw Markdown still exists on disk, so a later scan can recreate missing Markdown if the original input is still available. This keeps a hand-pruned `raw/` tree and the manifest from drifting apart.

## What is safe to edit by hand

Avoid editing source or wiki files by hand during a run; external changes can still interrupt processing.

- **`wiki/*.md`** is yours (or an agent's) to read, edit, and reorganize. Edits stay scoped to the compiled pages, never `raw/` or the manifest, and committing your own changes is fine. Avoid history surgery by hand: undoing a build or recovering needs the manifest and links to stay in sync, so prefer using the wiki maintenance commands (see [command line](cli.md#wiki-maintenance)).
- **`<vault>/config.yaml`** holds pipeline settings and watched folders. Settings validates and rewrites this file, so saving can replace YAML comments and formatting. Manual edits are picked up on subsequent reads; accepted jobs retain their captured settings.

Avoid hand-editing these:

- **`manifest.db` and `search.db`**: use the app or API for source operations. The backend manages database records and rebuilds the search index.
- **`wiki/_meta/` and `wiki/_views/`**: builds update the topic schema and regenerate navigation. Use domain actions to keep the schema and page metadata in agreement.
