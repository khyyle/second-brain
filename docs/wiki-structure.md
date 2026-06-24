# Wiki structure

This document explains what the wiki contains, how it works, and the structure it gives. For the full vault layout and the rest of the pipeline's files, see [architecture](architecture.md#where-data-lives).

## The wiki

The wiki is the `wiki/` subtree of the vault root. It contains plain Markdown, so it opens directly as an Obsidian vault.

```
wiki/
├── concepts/  problems/  projects/  insights/   content pages — the graph nodes
├── _meta/
│   └── topic_schema.yaml      content types, the domain vocabulary, and rules
└── _views/                    generated browse aids
    ├── index.md  gaps.md  recently-updated.md
    └── domains/<domain>.md
```

### Pages are a flat, relational graph

The four content directories are **typed buckets** — they sort a page by what it *is* (a concept, a problem, a project, an insight), nothing more. These are intentionally flat, and do not contain subfolders. Topical organization instead comes from two places in *each* page:

- **Frontmatter `domains`** — a list, so one page can belong to several domains (e.g. a page is both `mathematics` and `computer-science`). A folder could only file it under one; the list cannot be expressed as a path. This is why domains are metadata, not directories.
- **`[[wikilinks]]` and backlinks** — the relationships between pages. The value of the wiki is this link graph, which is independent of where a file sits.

The per-domain pages under `_views/domains/` are generated from frontmatter, so a page appears under every domain it declares. This structure avoids forcing a single home for a file, which results in a queryable, traversable, and intuitive graph structure.

### Domains are emergent, not pre-defined

A new wiki is initialized with **no domains**. Over time, the compilation agent creates domains from your content, steered to prefer broad subject areas (e.g. `finance`, `biology`) and to reuse an existing one before inventing a new one. After each build the domains it used are registered back into `topic_schema.yaml`, which therefore acts as the canonical, reusable vocabulary. Because domains are just frontmatter, an over-narrow one can be renamed, merged, or removed later without recompiling.

## Source of truth vs. derived


| Holds the truth                     | Derived, rebuildable from the truth                |
| ----------------------------------- | -------------------------------------------------- |
| The page files (frontmatter + body) | `search.db` (keyword + embedding index)            |
| `_meta/topic_schema.yaml`           | `_views/` (index, gaps, recently-updated, domains) |
| `raw/` parsed sources               | the in-memory link graph the MCP traverses         |
| `manifest.db` (ingestion state)     | —                                                  |


Everything in the right column can be regenerated from the left. Deleting it costs only the time to rebuild.

## The two databases

`manifest.db`: authoritative *source and ingestion* state

| Table | Holds |
| --- | --- |
| `manifest` | per-source ingestion status, content hash, parse lane |
| `compiled` | which raw paths have been turned into pages |
| `triage` | the worthwhile / review / skip / deferred verdicts |
| `page_cache` | per-PDF-page OCR cache, keyed by image hash |

`search.db`: *wiki-derived index* that rebuilds from `wiki/` at any time

| Table | Holds |
| --- | --- |
| `wiki_fts` | full-text keyword search |
| `wiki_meta` | page metadata: title, type, domains, tags, path, hashes |
| `wiki_vec` | embeddings for semantic search |
