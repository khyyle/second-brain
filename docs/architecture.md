# Architecture

Second Brain is a local-first pipeline that turns scattered source material into one interlinked Markdown wiki. It runs as a short pipeline connected through the filesystem rather than a long-running service: each step reads what the previous one wrote under `~/second-brain/`, so any step can run on its own and the menu bar app is just a thin front end over the same files.

Two kinds of source take different paths through it. A document you drop — a PDF, a note — is already something you chose to keep, so it goes straight from parsing to compilation. A ChatGPT export is the opposite: a bulk dump where most conversations aren't worth keeping and many cover the same ground. So chats pick up two steps that documents skip — *triage* to weed out the noise, and *clustering* to group related conversations — before reaching the same compile step.

```
documents   capture -> ingest ----------------------> compile -> access
chats       capture -> ingest -> triage -> cluster -> compile -> access
```

Triage and clustering, then, are not stages everything flows through — they are the chat lane's way of turning a noisy export into something worth paying to compile.

## Stages

### Capture

Material enters either by being dropped onto the menu bar window or copied into `~/second-brain/drops/`. The drop zone copies files (never moves them) into a drop folder and validates as it goes: a real ChatGPT export is detected by its JSON shape and routed to the `chatgpt` lane, and everything else goes to `documents`. A scheduled run can also scan additional watched folders listed in `sources.json`.

### Ingest

Ingestion converts each source to Markdown under `~/second-brain/raw/`, mirroring the source layout (`raw/documents/`, `raw/chatgpt/`, and so on). The parser is chosen per file:

- PDFs are classified one page at a time. Born-digital pages go to Docling; handwritten or scanned pages go to Chandra. A document that mixes the two is parsed by both and stitched back together in reading order. Page classification keys on the font fingerprint — Apple handwriting exports bake in many San-Francisco font subsets from their OCR layer, which distinguishes them from genuinely typed PDFs.
- Plain text (`.md`, `.txt`, `.tex`) passes through with light front-matter added.
- A ChatGPT export is split into one Markdown file per conversation.

A SQLite manifest records what has been ingested so unchanged files are skipped on the next run.

### Triage

Bulk chat history is noisy. Most conversations are small talk, one-off lookups, or abandoned tangents, and compiling all of them would waste money on pages worth nothing. So before anything reaches the paid compilation step, a small local model (Gemma, via Ollama) reads each new chat and labels it worthwhile, review, or skip. This runs during ingestion — it is free and local — so every chat already has a verdict by the time you build.

Triage only looks at the lanes named in `triage.sources`, which is just ChatGPT by default. A document you dropped yourself never goes through triage at all: dropping it is the curation, so it carries no verdict and flows straight to the build. Chats marked review are copied into `~/second-brain/inbox/` for a manual pass.

Triage is built to never block the pipeline. If Ollama is off, or a chat comes back unscorable, that chat is passed through to the build rather than dropped. If a source vanishes mid-run (you un-ingested it from the app while triage was working), that one source is skipped and the run keeps going.

### Compile

Building the wiki is the only stage that calls a hosted model, so it is also where the money goes. Two things happen here: planning, then synthesis.

Planning is optional and exists to fight redundancy. Five years of chats circle the same topics over and over — a dozen separate conversations might all touch gradient descent — and compiling each on its own would pay to write a dozen overlapping pages. A clustering step groups related chats first so a topic can become one page instead of many. It embeds each staged chat locally (the same Ollama embeddings used for search) and groups them, either by a cosine-similarity threshold or by HDBSCAN density. Like triage, clustering only touches the lanes in `clustering.sources` (ChatGPT by default); a dropped document always stands on its own. You can preview a grouping before spending anything — the menu bar app writes the proposed plan to `.clusters.json`, shows you the clusters, and lets you split a group or pop a chat out, recording those tweaks in `.cluster-overrides.json`. The build then honors whatever plan you reviewed. For unattended runs, `clustering.enabled` lets a scheduled build cluster on its own without a human previewing first.

Synthesis is the agent. A Claude agent works through the plan one group at a time, reading that group's sources with sandboxed file tools and writing or updating wiki pages with YAML front matter, `[[wikilinks]]`, LaTeX math, and source citations. A chat that did not cluster with anything is simply a group of one. Cross-linking across groups still works because the agent reads the growing wiki through those same tools. Two guards bound spend: a per-run token budget caps how much the agent can do on one group, and an optional per-build dollar ceiling stops the run once cumulative cost crosses it; a group too large for a single run is split into smaller batches first.

After the agent pass, a deterministic step — no model — rebuilds the index, gap list, domain views, and recently-updated list from the file graph.

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
| Wiki synthesis (the build step) | Claude API |

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

A few dotfiles coordinate a run with the menu bar app: `.status.json` (a progress heartbeat), `.build-log.jsonl` (a created/updated history), `.stop` (a cooperative stop flag), and — once you preview clustering — `.clusters.json` (the proposed grouping) plus `.cluster-overrides.json` (your split/pop tweaks). The cluster files are disposable: a finished build consumes them, and re-previewing rewrites them from scratch.

Configuration lives in the repository, not the vault: `config/config.yaml` holds the pipeline settings and `.env` holds the Anthropic API key (owner-only, never committed).

## Hashing and caching

Two layers of caching keep repeated work cheap, and both are explained in detail in [the lifecycle document](lifecycle.md):

- Each source is fingerprinted by a content hash so a byte-identical file dropped again, even under a different name, is not reprocessed.
- Each rendered PDF page is fingerprinted by the hash of its image, so re-exporting a notebook after editing one page only re-runs OCR on the page that actually changed.
