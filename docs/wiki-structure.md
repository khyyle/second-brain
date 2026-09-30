# Wiki structure

This document explains what the wiki contains, how it works, and the structure it gives. For the full vault layout and the rest of the pipeline's files, see [architecture](architecture.md#where-data-lives).

## The wiki

The wiki is the `wiki/` subtree of the vault root. It contains plain Markdown, so it opens directly as an Obsidian vault.

```
wiki/
├── concepts/  problems/  projects/  papers/  insights/   content pages — the graph nodes
├── _meta/
│   └── topic_schema.yaml      content types, the domain vocabulary, and rules
└── _views/                    generated browse aids
    ├── index.md  gaps.md  recently-updated.md
    └── domains/<domain>.md
```

### Pages are a flat, relational graph

The five content directories are **typed buckets** that sort a page by what it *is* (a concept, a problem, a project, a paper, an insight), nothing more. These are intentionally flat, and do not contain subfolders; topical organization instead comes from two places in *each* page:

- **Frontmatter `domains`** — a list, so one page can belong to several domains (e.g. a page is both `mathematics` and `computer-science`). A folder could only file it under one; the list cannot be expressed as a path. This is why domains are metadata, not directories.
- **`[[wikilinks]]` and backlinks** — the relationships between pages. The value of the wiki is this link graph, which is independent of where a file sits.

The buckets were chosen to mirror the hierarchy of knowledge, minimizing redundancy and making linkage powerful. Concepts are axiomatic or foundational knowledge that the other buckets reference. Problems and projects are applications and realizations of that knowledge; holding them separately lets them refer to a concept without rewriting it. Papers are cross-cutting, both building upon concepts and feeding novel contributions back into them; they are given their own bucket so that a paper's argument (e.g. its problem, contribution, and key results) stays referenceable as a whole, while the methods it introduces live on as concepts. Insights record realizations synthesized across sources, connections that belong to no single concept.

The per-domain pages under `_views/domains/` are generated from frontmatter, so a page appears under every domain it declares. This structure avoids forcing a single home for a file, which results in a queryable, traversable, and intuitive graph structure.

### Gaps and orphans

The wiki is deliberately allowed to link to concepts that have no page yet. A page that builds on an unwritten fundamental links it anyway, acting as a record of where that concept is needed. Obsidian renders these links muted rather than broken, and when a later build writes a page with that name, every existing reference to it resolves on its own. This can also serve as a pointer to what to add to your knowledge base next.

- **Gaps** ("referenced but not written"): a prerequisite, related link, or body link that names a concept with no page. A gap is a concept the wiki leans on but has not written up. Ranked by how many pages reference it, the list reads as a demand-ordered "write this next" list.
- **Orphans** ("not linked from any page"): a page nothing else links to. It is still reachable by search and the index, but linking to it from a related page makes it easier to find while browsing.

Both are surfaced in the app's Overview tab. Gaps are also written to `_views/gaps.md` and queryable over MCP with `list_gaps`.

### Domains are emergent

A new wiki is initialized with **no domains**. Over time, the compilation agent creates domains from your content, steered to prefer broad subject areas (e.g. `finance`, `biology`) and to reuse an existing one before inventing a new one. After each build the domains it used are registered back into `topic_schema.yaml`, which therefore acts as the canonical, reusable vocabulary. Because domains are just frontmatter, an over-narrow one can be renamed, merged, or removed later without recompiling.

## Source of truth vs. derived


| Holds the truth                     | Derived, rebuildable from the truth                |
| ----------------------------------- | -------------------------------------------------- |
| The page files (frontmatter + body) | `search.db` (keyword + embedding index)            |
| `_meta/topic_schema.yaml`           | `_views/` (index, gaps, recently-updated, domains) |
| `raw/` parsed sources               | the link graph the MCP traverses         |
| `manifest.db` (ingestion state)     | —                                                  |


Everything in the right column can be regenerated from the left. Deleting it costs only the time to rebuild.

## The two databases

`manifest.db`: authoritative *source and ingestion* state

| Table | Holds |
| --- | --- |
| `manifest` | per-source ingestion status, content hash, parse lane |
| `compiled` | which raw paths have been turned into pages |
| `triage` | the kept / review / skip verdicts |
| `page_cache` | per-PDF-page OCR cache, keyed by image hash |

`search.db`: *wiki-derived index* that rebuilds from `wiki/` at any time

| Table | Holds |
| --- | --- |
| `wiki_fts` | full-text keyword search |
| `wiki_meta` | page metadata: title, type, domains, tags, path, hashes |
| `wiki_vec` | embeddings for semantic search |
| `wiki_links` | typed page-to-page link graph (prerequisite, related, tests, uses, mention) |
