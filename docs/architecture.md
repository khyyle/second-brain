# Architecture

Second Brain is a local-first pipeline that turns scattered source material into one interlinked Markdown wiki. It runs as a short pipeline connected through the filesystem rather than a long-running service: each step reads what the previous one wrote under `~/second-brain/`, so any step can run on its own and the menu bar app is just a thin front end over the same files.

Two kinds of source take different paths through it. A document you drop goes straight from ingest to compile. A bulk ChatGPT export picks up two extra steps: triage to weed out the noise and clustering to group related conversations, so a noisy export becomes something worth paying to compile.

```
documents   capture -> ingest ----------------------> compile -> access
chats       capture -> ingest -> triage -> cluster -> compile -> access
```

## Stages

### Capture

Material enters either by being dropped onto the menu bar window or copied into `~/second-brain/drops/`. The drop zone copies files (never moves them) into a drop folder and validates as it goes. A real ChatGPT export is detected by its JSON shape and routed to the `chatgpt` lane, and everything else goes to `documents`. A scheduled run can also scan additional watched folders listed in `sources.json`.

### Ingest

Ingestion converts each source to Markdown under `~/second-brain/raw/`, mirroring the source layout (`raw/documents/`, `raw/chatgpt/`, and so on). The parser is chosen per file:

- PDFs are classified one page at a time. Born-digital pages go to Docling; handwritten or scanned pages go to Chandra. A document that mixes the two is parsed by both and stitched back together in reading order.
- Plain text (`.md`, `.txt`, `.tex`) passes through with light front-matter added.
- A ChatGPT export is split into one Markdown file per conversation.

A SQLite manifest records what has been ingested so unchanged files are skipped on the next run.

### Triage

Bulk chat history is noisy. Conversations can be small talk, one-off lookups, or abandoned tangents, so compilation might be wasted on pages worth nothing. During ingestion (before anything reaches the paid compilation step), a small local model (Gemma, via Ollama) reads each new chat and labels it worthwhile, review, or skip.

Triage only looks at the lanes named in `triage.sources`, which is just ChatGPT by default. A document you dropped yourself never goes through triage at all: dropping it is the curation, so it carries no verdict and flows straight to the build. Chats marked review are copied into `~/second-brain/inbox/` for a manual pass.

Triage is built to never block the pipeline. If Ollama is off, or a chat comes back unscorable, that chat is passed through to the build rather than dropped. If a source vanishes mid-run (you un-ingested it from the app while triage was working), that one source is skipped and the run keeps going.

### Compile

Building the wiki is the only stage that calls a hosted model, so it is also where the money goes. Two things happen here: planning, then synthesis.

Planning is optional and exists to fight redundancy. Years of chats might circle the same topics (e.g. multiple conversations talking about gradient descent); compiling each of these on their own is likely to be wasteful and result in overlapping pages that would hinder the quality of the wiki. Thus, a clustering step groups related chats first so a topic can become one page instead of many. It embeds each staged chat locally and groups them. Like triage, clustering only touches the lanes in `clustering.sources` (ChatGPT by default). You can preview a grouping before spending anything, adjust it in the app, and the build honors the plan you reviewed (see [using the app](using-the-app.md#building-the-wiki)). For unattended runs, `clustering.enabled` lets a scheduled build cluster on its own without a human previewing first.

Synthesis is done by an agent. It works through the plan one group at a time, reading that group's sources with sandboxed file tools and writing or updating wiki pages with YAML front matter, `[[wikilinks]]`, LaTeX math, and source citations. A chat that did not cluster with anything is simply a group of one. Cross-linking across groups still works because the agent reads the growing wiki through those same tools. Spend is bounded by an optional per-build dollar ceiling, checked between groups and as each run progresses; a group too large for a single run is split into smaller batches first. A run that ends without completing cleanly is rolled back and set aside with a visible reason rather than retried unattended.

After the agent pass, a deterministic step rebuilds the index, gap list, domain views, and recently-updated list from the file graph.

The wiki's own git history is updated as the build progresses, so a stopped or failed run keeps everything already completed. See [how the wiki is versioned](lifecycle.md#how-the-wiki-is-versioned).

### Access

The compiled wiki is plain Markdown under `~/second-brain/wiki/`, so it opens directly as an Obsidian vault. An MCP server exposes it to assistants such as Claude Desktop and Cursor, always offering keyword search and adding embedding-based semantic search when Ollama is available.

## What runs locally versus in the cloud

| Work | Where it runs |
| --- | --- |
| PDF layout and typed-page parsing (Docling) | Local |
| Handwriting and scanned-page OCR (Chandra) | Local, on the MLX backend on Apple Silicon |
| Triage classification (Gemma) | Local, via Ollama |
| Semantic-search and clustering embeddings | Local, via Ollama |
| Grouping chats into clusters | Local (threshold or HDBSCAN over embeddings) |
| Wiki synthesis (the build step) | Cloud via the configured provider (Anthropic or DeepSeek) |

Only the build step is required to leave the machine. Everything needed to capture, parse, and filter your material is local and free.

## Where data lives

Everything sits under the vault root, `~/second-brain/`:

```
~/second-brain/
├── drops/          capture queue. Files land here and are removed once ingested
├── raw/            parsed Markdown, one tree per source lane and read during compilation
├── wiki/           the compiled knowledge base (plain Markdown, git-tracked)
├── inbox/          copies of review-tier sources awaiting a manual decision
├── logs/           run logs, including pipeline.log for detached runs
├── manifest.db     ingestion state, page cache, and triage decisions
├── search.db       keyword + embedding index, rebuilt from the wiki
├── sources.json    GUI-managed list of watched folders
└── (dotfiles)      run coordination with the menu bar app — see below
```

`manifest.db` and `search.db` are detailed in [wiki structure](wiki-structure.md#the-two-databases).

A few dotfiles coordinate a run with the menu bar app: `.status.json` (a progress heartbeat), `.build-log.jsonl` (a created/updated history), `.stop` (a cooperative stop flag), and, once you preview clustering, `.clusters.json` plus `.cluster-overrides.json` (the proposed grouping and your tweaks to it, described in [the lifecycle document](lifecycle.md#what-gets-recorded)).

Configuration lives in the repository: `config/config.yaml` holds the pipeline settings and `.env` holds the compilation provider's API key (owner-only, never committed).

## Hashing and caching

Two layers of caching keep repeated work cheap, and both are explained in detail in [the lifecycle document](lifecycle.md):

- Each source is fingerprinted by a content hash so a byte-identical file dropped again, even under a different name, is not reprocessed.
- Each rendered PDF page is fingerprinted by the hash of its image, so re-exporting a notebook after editing one page only re-runs OCR on the page that actually changed.
