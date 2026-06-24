# Wiki structure

This document describes the on-disk layout of the vault — what each file and directory is, which ones hold the source of truth, and which are derived and rebuildable. For how the pipeline produces these, see [architecture](architecture.md).

## The vault

Everything lives under the vault root, `~/second-brain/`:

```
~/second-brain/
├── drops/          capture queue; files land here and are removed once ingested
├── raw/            parsed Markdown, one tree per source lane (raw/chatgpt/, raw/documents/)
├── wiki/           the compiled knowledge base (git-tracked)
├── inbox/          copies of review-tier chats awaiting a manual decision
├── logs/           run logs, including pipeline.log for detached runs
├── manifest.db     SQLite: ingestion and source state
├── search.db       SQLite: the wiki-derived search index
├── sources.json    GUI-managed list of watched folders
└── (dotfiles)      run coordination — see below
```

Configuration is not in the vault: `config/config.yaml` (pipeline settings) and `.env` (API keys) live in the repository.

## The wiki

`wiki/` is plain Markdown so it opens directly as an Obsidian vault.

```
wiki/
├── concepts/  problems/  projects/  insights/   content pages — the graph nodes
├── _meta/
│   ├── topic_schema.yaml      the schema: domains, content types, and rules
│   ├── schema_proposals.yaml  new domains the agent proposes for review
│   └── backlinks.json         generated incoming-link map (for Obsidian)
└── _views/                    generated browse aids
    ├── index.md  gaps.md  recently-updated.md
    └── domains/<domain>.md
```

### Pages are a flat, relational graph

The four content directories are **typed buckets** — they sort a page by what it *is* (a concept, a problem, a project, an insight), nothing more. They are flat: a page lives directly in its bucket, e.g. `concepts/gradient-descent.md`, never in a subfolder.

Topical organization is **not** folders. It comes from two places in each page:

- **Frontmatter `domains`** — a list, so one page can belong to several domains (e.g. a page is both `mathematics` and `computer-science`). A folder could only file it under one; the list cannot be expressed as a path. This is why domains are metadata, not directories.
- **`[[wikilinks]]` and backlinks** — the relationships between pages. The value of the wiki is this link graph, which is independent of where a file sits.

The per-domain pages under `_views/domains/` are generated from frontmatter, so a page appears under every domain it declares. That is the browsing affordance folders would otherwise provide, without forcing a single home.

## Source of truth vs. derived

| Holds the truth | Derived, rebuildable from the truth |
| --- | --- |
| The page files (frontmatter + body) | `search.db` (keyword + embedding index) |
| `_meta/topic_schema.yaml` | `_views/` (index, gaps, recently-updated, domains) |
| `raw/` parsed sources | `_meta/backlinks.json` |
| `manifest.db` (ingestion state) | the in-memory link graph the MCP traverses |

Everything in the right column can be regenerated from the left. Deleting it costs only the time to rebuild.

## The two databases

They are split by concern and lifecycle, and are not interchangeable:

- **`manifest.db`** is *source and ingestion* state — authoritative and persistent. Tables: `manifest` (per-source ingestion status, hash, parse lane), `compiled` (which raw paths have been turned into pages), `triage` (the worthwhile / review / skip / deferred verdicts), and `page_cache` (per-PDF-page OCR cache keyed by image hash).
- **`search.db`** is the *wiki-derived index* — rebuildable from `wiki/` at any time. Tables: `wiki_fts` (full-text keyword search), `wiki_meta` (page metadata: title, type, domains, tags, path, hashes), and `wiki_vec` (embeddings for semantic search).

## Coordination dotfiles

These live at the vault root and let a pipeline run talk to the menu bar app:

- `.status.json` — a progress heartbeat (current phase, i/n, elapsed, cost).
- `.build-log.jsonl` — an append-only created/updated history of wiki pages.
- `.stop` — a cooperative cancel flag the build polls between steps.
- `.clusters.json` / `.cluster-overrides.json` — the previewed chat grouping and your split/pop tweaks; consumed by a finished build.
- `.pipeline-script` — a pointer the installer writes so the app can locate `run.sh`.

`sources.json` is the watched-folder list managed by Settings → Automation; it is merged into the source configuration at load so the built-in drop folders always take precedence.

## Where this lives in code

The wiki's data model — page discovery, the link graph, the schema, and health analysis — is the `second_brain/wiki/` package (`structure.py`, `schema.py`, `health.py`). It sits below both the compilation pipeline that writes the wiki and the MCP server that serves it; both depend on it.
