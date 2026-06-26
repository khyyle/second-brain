# Querying over MCP

Second Brain ships an MCP server that connects the compiled wiki to a tool-using assistant like Claude Desktop or Cursor, so you can ask questions of your notes in plain language instead of opening and searching files yourself. The server runs on your machine and reads only your local vault.

What makes this more than search is that the wiki is a *graph*, not a pile of pages (see [wiki structure](wiki-structure.md#pages-are-a-flat-relational-graph)). Pages are joined by typed links (ex: one concept is a prerequisite for another, two are related, a third is only mentioned) and the server lets an assistant walk those links, not just match words against them. The tools fall into four groups: finding pages, following the graph between them, tracing a page back to the sources it was built from, and capturing new material into the pipeline.

## Connecting an assistant

In the GUI's Settings a one-click button connects each supported assistant. Alternatively, you can install the MCP from terminal:

```bash
uv run second-brain mcp install --target claude-desktop   # or: cursor
```

> Either way, restart the assistant afterwards so it picks up the new server.

## Finding pages

These are the entry points — how an assistant locates the page or pages a question is about.

- **`search_wiki`** keyword-searches page text, ranked by relevance. It is the right tool for exact terms and names, and it understands query operators: phrases, `AND`/`OR`/`NOT`, and field filters like `domains:finance`.
- **`semantic_search`** matches by meaning rather than words, so it surfaces the right pages even when your wording never appears in them — better for fuzzy or conceptual lookups. This layer needs Ollama running; without it the server falls back to keyword search.
- **`list_domains`** and **`read_index`** map what the wiki covers — the domains with their page counts, or a one-shot overview of every domain and its pages. An assistant new to your vault usually starts here.
- **`list_pages`** filters by domain, tag, or content type (`concept`, `problem`, `project`, `insight`) when there is a known slice to browse rather than a phrase to search.
- **`read_page`** pulls a single page in full once it has been located.

## Following the graph

This is the part a plain folder of Markdown cannot do. Having found a page, an assistant can traverse the relationships around it.

- **`find_related`** answers the open-ended "what touches this?", walking outward over every kind of link, in either direction, up to a chosen number of hops.
- **`prerequisite_closure`** answers "what do I need to understand first?". It follows prerequisite links transitively and returns the concepts in learning order (fundamentals first, the topic itself last) so the result reads as a derivation from the ground up. Concepts that are referenced but not yet written are flagged as gaps.
- **`dependents`** is the reverse: the pages that build on this one by naming it a prerequisite, which is to say where a concept leads next.
- **`list_gaps`** lists the concepts your pages refer to but never actually cover, ranked by how many pages want them. These are the wiki's known holes, and a direct answer to "what should I write or learn next?".

## Tracing a page to its sources

Every wiki page records the raw documents it was compiled from, so a claim can always be taken back to where it came from.

- **`get_sources_summary`** previews a page's sources — each one's frontmatter and opening paragraph — which is usually enough to judge which are worth reading in full.
- **`get_sources`** then returns the full text of those source documents, for verifying a claim or expanding a page from the original material.

## Capturing new material

- **`capture_note`** writes a freeform insight from the conversation into the drop queue, where it flows through the normal ingest, triage, and compile path like anything else you add. It does not author a finished page directly: the next build titles it, places it, and links it into the graph.


## Tool reference


| Tool                   | What it does                                               |
| ---------------------- | ---------------------------------------------------------- |
| `search_wiki`          | Keyword search over page text (BM25), with query operators |
| `semantic_search`      | Meaning-based search using embeddings                      |
| `read_page`            | Read one page in full by title or slug                     |
| `list_pages`           | List pages filtered by domain, tag, or content type        |
| `read_index`           | One-shot overview of every domain and its pages            |
| `list_domains`         | The domains in the wiki, with page counts                  |
| `find_related`         | Pages linked to a page, any direction, any link type       |
| `prerequisite_closure` | A page's transitive prerequisites, in learning order       |
| `dependents`           | The pages that build on a page as a prerequisite           |
| `list_gaps`            | Referenced-but-unwritten concepts, most-wanted first       |
| `get_sources_summary`  | A light preview of the sources behind a page               |
| `get_sources`          | The full source documents a page was compiled from         |
| `capture_note`         | Save a freeform insight back into the pipeline             |


