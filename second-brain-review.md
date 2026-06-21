# Second Brain Pipeline — Architecture Review & Recommendations

> Compiled from design review session. Assumes familiarity with the existing codebase:
> `chatgpt_parser.py`, `compiler.py`, `structure.py`, `agent_prompt.py`, `cli.py`, MCP server.

---

## 1. Foundational Philosophy

Before specifics, the three principles that should govern every design decision:

**1. Folders answer "where do I browse this?" — metadata and links answer "what is this related to?"**
The folder tree is a browse hint, not an ontology. Don't let path depth carry semantic burden.

**2. Deterministic rebuild is your superpower.**
Because `rebuild_structure` is pure filesystem graph analysis with no LLM, you can afford an imperfect shallow hierarchy. The index, backlinks, domain views, and gap analysis are always derivable from ground truth. Never hand-author these.

**3. The wiki only contains things you've validated. Raw is not the wiki.**
The inbox/triage layer is what keeps the wiki trustworthy over time. Without it, one bad ingestion pass contaminates the whole graph.

---

## 2. Knowledge Architecture

### 2.1 Hierarchy Depth

**Recommendation: shallow physical hierarchy + rich logical structure.**

```
wiki/<domain>/<optional-subdomain>/<page>.md
```

Never deeper than 2 levels for concept pages. The rule set:

```yaml
placement_policy:
  max_depth: 2
  folder_semantics: browse_only
  canonical_home_required: true
  multi_domain_membership_allowed: true
  subdomain_creation_threshold_pages: 10
  subdomain_creation_threshold_links: 25
  cross_domain_preferred_for_shared_foundations: true
  separate_content_types: [concept, problem, project, synthesis]
```

Don't go fully flat (Andrej-style) because the vault is also a human browse layer in Obsidian. Some hierarchy compresses navigation meaningfully. Don't go deep because folder path starts to masquerade as ontology, and ambiguous concepts (optimization, game theory, Bayes) don't belong cleanly to one place.

### 2.2 Content Types

**The most important structural decision in the whole system.**

Mixing ontological levels is where every personal wiki eventually collapses. These are not the same kind of object:

- `expected-value` → concept
- `dice-games` → problem family  
- `game-theory` → domain/topic

Enforce content type in frontmatter and let the deterministic index generate separate views per type:

```yaml
content_type: concept | problem | project | synthesis | insight
```

Current `CONTENT_DIRS = ("concepts", "problems", "projects", "insights")` is correct. Add `syntheses/` for conversation-derived distillations.

### 2.3 Cross-Domain is Central, Not a Catch-All

For a quant/ML/CS knowledge base, easily **40–50% of the most important concepts are cross-domain**: optimization, probability, linear algebra, information theory, dynamic programming, Fourier analysis, Bayes, regularization, likelihood, inference.

`cross-domain/` being your biggest folder is correct, not a sign something went wrong. Use it aggressively.

### 2.4 Replace `applied/` with Purposeful Folders

The current `applied/` folder containing `quant-finance`, `leetcode`, `startups`, and `health-algorithms` is a junk drawer. These are contexts of use, not a coherent knowledge family.

Replace with:

- `projects/` — things being built (Nexa, Shiboleth, systems)
- `interview-prep/` — leetcode, QR drills, brainteasers
- `contexts/` — thin pages representing "concept X in domain Y", linking back to canonical cross-domain pages

Example: `contexts/quant-finance/black-scholes.md` is a thin page pointing to `cross-domain/stochastic-calculus.md` and `cross-domain/PDEs.md`. It doesn't duplicate content.

### 2.5 Canonical Home + Metadata Membership

Every concept gets one canonical home, many logical memberships:

```yaml
---
title: Optimization
canonical_path: cross-domain/optimization.md
domains: [mathematics, computer-science, economics]
subdomains: [calculus, machine-learning, finance]
aliases: [mathematical optimization, numerical optimization]
tags: [gradient-descent, convexity, lagrangian, portfolio]
---
```

"Optimization" lives physically in `cross-domain/`, but is surfaced under math, ML, and finance domain views via the deterministic rebuild.

---

## 3. Corpus Sizing & Implications

**Rough token math for your corpus:**

| Source | Volume | Raw tokens (est.) |
|---|---|---|
| GoodNotes | ~500 pages | ~250k tokens post-OCR |
| Chat histories | 1000 chats × 20 messages | ~4M tokens |
| Documents | variable | variable |

**4M tokens of chat history alone exceeds any single context window.** This makes the triage layer non-optional, not just nice-to-have.

The chat histories also need heavier synthesis than anything else. Most individual conversations compress to nothing worth keeping. Estimate ~10–20% produce a genuine insight worth promoting to the wiki. The rest should be archived, not ingested.

---

## 4. Pipeline Architecture

### 4.1 Recommended Five-Stage Pipeline

```
Stage 1 — Ingest & OCR
  GoodNotes PDFs → Chandra (85% benchmark) → raw markdown in raw/goodnotes/
  ChatGPT exports → chatgpt_parser.py → raw markdown in raw/chatgpt/
  Desktop folders → watchdog → raw/documents/

Stage 2 — Triage (cheap model, local)
  Gemma 4 or similar small local model
  Per raw file: worthwhile | review | skip
  Extracts: concept hints, content_type guess, domain hints
  "skip" → manifest.mark_skipped(), never hits Claude
  "review" → inbox/ for weekly human pass
  "worthwhile" → proceeds to Stage 3

Stage 3 — Synthesis (Claude Sonnet, API)
  Only receives "worthwhile" items from Stage 2
  Agentic tool-use loop (read/write/edit/glob/grep)
  Writes/updates wiki pages with frontmatter, wikilinks, LaTeX, citations
  Hard token budget per run

Stage 4 — Structure Rebuild (deterministic, no LLM)
  rebuild_structure(wiki_dir)
  Generates: index.md, backlinks.json, domain views, gaps.md, recently-updated.md
  Always derived from filesystem, never hand-authored

Stage 5 — Watchdog & Commit
  File watcher on source folders (watchdog library)
  New files → Stage 1
  Changed files → re-triage → re-synthesize if needed
  git auto-commit after successful rebuild
```

### 4.2 Inbox Flow (Currently Missing)

There is no mechanism for surfacing `review` tier items to you. Add:

```
inbox/
  goodnotes/     # triage said "review" — OCR quality issues or ambiguous content
  chats/         # triage said "review" — maybe worth synthesizing
  weekly-digest.md  # auto-generated list of inbox items with triage summaries
```

Weekly digest generated deterministically after each pipeline run. You spend 15 minutes reviewing, promoting good items to wiki/, archiving the rest.

---

## 5. Code-Level Issues & Fixes

### 5.1 `discover_all_pages` — Silent Subdomain Miss

**File:** `structure.py`

```python
# CURRENT — misses anything in subdomain folders
for md_file in dir_path.glob("*.md"):

# FIX — walk all depths
for md_file in dir_path.rglob("*.md"):
```

This is a critical bug. With subdomains like `concepts/mathematics/`, everything in them is invisible to the link graph, backlinks, orphan detection, gap analysis, and index generation. The entire deterministic rebuild operates on an incomplete picture of the vault.

### 5.2 Compilation Agent — Token Budget Guard

**File:** `compiler.py`

The current loop runs up to 50 iterations with an ever-growing messages list (every tool result accumulates). For a batch of 10 sources this can easily exceed $10–15 per run if the agent gets confused or keeps "improving" pages beyond the batch.

```python
def _run_agent(config, wiki_dir, raw_dir, sources):
    client = anthropic.Anthropic()
    executor = _WikiToolExecutor(wiki_dir, raw_dir)
    prompt = build_compilation_prompt(sources, wiki_dir)
    messages = [{"role": "user", "content": prompt}]
    
    total_input_tokens = 0
    total_output_tokens = 0
    TOKEN_BUDGET = 150_000  # tune per your cost tolerance
    
    for iteration in range(50):
        response = client.messages.create(
            model=config.compilation.model,
            max_tokens=8192,
            system=COMPILATION_SYSTEM_PROMPT,
            tools=WIKI_TOOLS,
            messages=messages,
        )
        
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        
        logger.debug(
            "Iteration %d: +%d input, +%d output tokens (total: %d)",
            iteration,
            response.usage.input_tokens,
            response.usage.output_tokens,
            total_input_tokens + total_output_tokens,
        )
        
        messages.append({"role": "assistant", "content": response.content})
        
        if response.stop_reason == "end_turn":
            logger.info("Agent completed after %d iterations", iteration + 1)
            break
        
        if (total_input_tokens + total_output_tokens) > TOKEN_BUDGET:
            logger.warning(
                "Token budget exceeded at iteration %d (%d tokens). Stopping.",
                iteration,
                total_input_tokens + total_output_tokens,
            )
            break
        
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = executor.execute(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        
        if not tool_results:
            break
        
        messages.append({"role": "user", "content": tool_results})
    
    logger.info(
        "Agent finished: %d iterations, %d input tokens, %d output tokens, %d changes",
        iteration + 1,
        total_input_tokens,
        total_output_tokens,
        len(executor.changes),
    )
```

**Additionally:** Consider trimming the messages history every 10 iterations. Tool results from turns 1–5 don't need to stay verbatim in context by iteration 20. Keep only the last N tool exchanges plus the original prompt.

### 5.3 Compilation Agent — Missing Termination Condition

**File:** `agent_prompt.py`

Without an explicit stop instruction, the agent sometimes continues "improving" existing pages after finishing the assigned batch. Add to end of `COMPILATION_SYSTEM_PROMPT`:

```python
COMPILATION_SYSTEM_PROMPT = """
...existing content...

## Termination
Once you have processed every source document in the provided list and 
written or updated all relevant wiki pages, stop immediately. Do not 
continue editing pages that were not directly affected by the new sources. 
Do not attempt to improve existing pages beyond what the new sources warrant.
When done, summarize what was created or updated and end your turn.
"""
```

### 5.4 Cheap Pre-Filter Before LLM Triage

Before hitting Gemma or Claude, apply a heuristic pass that eliminates obvious noise for free:

```python
def should_triage(conv: ParseResult) -> bool:
    """
    Fast heuristic filter — eliminates obvious low-value conversations
    before any LLM call.
    """
    word_count = sum(len(b.content.split()) for b in conv.blocks)
    user_turns = sum(1 for b in conv.blocks 
                     if b.content.startswith("**User**"))
    
    # Too short to contain meaningful knowledge
    if word_count < 500:
        return False
    
    # Mostly assistant monologue (clarification chains, not knowledge exchange)
    if user_turns < 3:
        return False
    
    # Single-turn Q&A (lookup, not synthesis)
    if user_turns == 1:
        return False
    
    return True
```

This alone eliminates ~30–40% of conversations before touching any model. Integrate into `_find_new_sources` or as a pre-filter step in `run_compilation`.

### 5.5 `get_sources` Token Cost in MCP

**File:** `mcp_server/tools.py`

`get_sources` returning full raw source documents is expensive when called in a chat session — each call can dump thousands of tokens into context. Add a summary variant:

```python
@mcp.tool()
def get_sources_summary(title: str) -> str:
    """
    Retrieve a lightweight summary of source documents for a wiki page.
    Returns frontmatter + first paragraph only, not full content.
    Use get_sources() only when full source text is needed.
    """
    return _get_tools().get_sources_summary(title)
```

---

## 6. Triage Architecture — Gemma 4 as Watchdog

### 6.1 Why Local Model for Triage

- ~1000 chat histories × Claude API cost per call = significant spend just to decide what's worth synthesizing
- Gemma 4 running locally via Ollama is effectively free per call
- Triage is a classification task, not a synthesis task — smaller model is sufficient
- Keeps the expensive model (Claude Sonnet) reserved for actual knowledge synthesis

### 6.2 Triage Module Design

```python
# second_brain/triage/gemma.py

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import httpx
import json

class TriageDecision(str, Enum):
    WORTHWHILE = "worthwhile"   # → Stage 3 (Claude synthesis)
    REVIEW = "review"           # → inbox/ (human weekly pass)
    SKIP = "skip"               # → manifest.mark_skipped()

@dataclass
class TriageResult:
    decision: TriageDecision
    confidence: float
    concept_hints: list[str]        # ["optimization", "gradient descent"]
    content_type_hint: str          # "concept" | "problem" | "project" | "insight"
    domain_hints: list[str]         # ["mathematics", "machine-learning"]
    reason: str                     # brief explanation for logging/review digest

TRIAGE_PROMPT = """\
You are a knowledge triage agent. Given a raw document (conversation, notes, 
or other source), decide whether it contains knowledge worth synthesizing into 
a personal wiki.

Respond ONLY with valid JSON matching this schema:
{
  "decision": "worthwhile" | "review" | "skip",
  "confidence": 0.0-1.0,
  "concept_hints": ["list", "of", "key", "concepts"],
  "content_type_hint": "concept" | "problem" | "project" | "insight",
  "domain_hints": ["domain1", "domain2"],
  "reason": "one sentence explanation"
}

Decision criteria:
- worthwhile: contains substantive knowledge, synthesis, conclusions, or worked examples
- review: ambiguous value, poor OCR quality, or partially relevant
- skip: logistics, small talk, simple lookups, abandoned tangents, duplicates

Be aggressive with skip — most conversations are not worth synthesizing.
"""

def triage_raw_file(raw_path: Path, ollama_host: str = "http://localhost:11434") -> TriageResult:
    content = raw_path.read_text(encoding="utf-8")
    
    # Truncate to avoid overwhelming small model
    if len(content) > 8000:
        content = content[:8000] + "\n\n[... truncated for triage ...]"
    
    payload = {
        "model": "gemma3:4b",  # or whichever variant you run
        "prompt": f"{TRIAGE_PROMPT}\n\nDocument:\n{content}",
        "stream": False,
        "format": "json",
    }
    
    resp = httpx.post(f"{ollama_host}/api/generate", json=payload, timeout=60)
    resp.raise_for_status()
    
    raw = resp.json()["response"]
    data = json.loads(raw)
    
    return TriageResult(
        decision=TriageDecision(data["decision"]),
        confidence=float(data["confidence"]),
        concept_hints=data.get("concept_hints", []),
        content_type_hint=data.get("content_type_hint", "insight"),
        domain_hints=data.get("domain_hints", []),
        reason=data.get("reason", ""),
    )
```

### 6.3 Integrating Triage into the Pipeline

In `compiler.py`, wrap `_find_new_sources` output through triage before passing to the agent:

```python
def _triage_sources(
    config: Config,
    raw_sources: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Returns: (worthwhile, review, skip) — relative raw paths.
    Falls back to passing everything through if Gemma unavailable.
    """
    worthwhile, review, skip = [], [], []
    
    for rel_path in raw_sources:
        raw_path = config.raw_dir / rel_path
        
        # Heuristic pre-filter (free)
        content = raw_path.read_text(encoding="utf-8")
        word_count = len(content.split())
        if word_count < 300:
            skip.append(rel_path)
            continue
        
        try:
            result = triage_raw_file(raw_path, config.search.ollama_host)
            if result.decision == TriageDecision.WORTHWHILE:
                worthwhile.append(rel_path)
            elif result.decision == TriageDecision.REVIEW:
                review.append(rel_path)
            else:
                skip.append(rel_path)
        except Exception as e:
            # Gemma unavailable — pass through to Claude
            logger.warning("Triage failed for %s: %s — passing through", rel_path, e)
            worthwhile.append(rel_path)
    
    logger.info(
        "Triage: %d worthwhile, %d review, %d skip (of %d total)",
        len(worthwhile), len(review), len(skip), len(raw_sources)
    )
    return worthwhile, review, skip
```

---

## 7. Temporal Dimension — The Missing Layer

Your GoodNotes and chat histories are time-stamped thinking. They represent you at a point in time — sometimes wrong, sometimes half-baked, sometimes exploring something you later abandoned.

**Problem:** If you ingest raw source material directly into wiki concept pages, you pollute the wiki with outdated thinking. A note from 18 months ago where you had the wrong mental model of Bayesian inference shouldn't overwrite your current understanding.

**Solution: Two-pass synthesis for time-sensitive sources**

For `chatgpt/` and `goodnotes/` sources specifically, the compilation agent should:

1. Check if a related concept page already exists
2. If yes: annotate the existing page with `> [!NOTE] Updated from [source] [date]` rather than overwriting
3. Only overwrite if the new source explicitly contradicts and corrects the existing content
4. Mark low-confidence or potentially outdated content with `⚠️` (already in your system prompt — good)

Add to `COMPILATION_SYSTEM_PROMPT`:

```
## Handling Temporal Sources
Chat histories and handwritten notes reflect thinking at a point in time.
When updating an existing page from these sources:
- Prefer additive edits (add a section, add a link) over rewrites
- If the source contradicts the existing page, add a ⚠️ note flagging the discrepancy
  rather than silently overwriting
- Preserve the existing page's structure — append, don't replace
- Only do a full rewrite if the source is clearly more complete and correct
```

---

## 8. Index Architecture for Large Vaults

### 8.1 The Core Discipline

For thousands of files, the index must describe the **shape** of the vault, not summarize the **content**. If the index grows proportionally to the vault, you're back to context exhaustion.

```
index.md contains:
  - What domains exist and roughly how many pages each
  - Which concepts are hubs (many backlinks)
  - Which areas are sparse (gaps)
  - Recently updated
  
index.md does NOT contain:
  - Page summaries
  - Content excerpts
  - Anything that grows linearly with page count
```

Target: index stays under 50–100k tokens regardless of vault size. At 1000 pages with concise listings, this is very achievable.

### 8.2 Hub Page Detection

Add to `rebuild_structure` — pages with many backlinks are navigation hubs and should be surfaced prominently in the index:

```python
def detect_hubs(
    pages: dict[str, WikiPage],
    graph: LinkGraph,
    threshold: int = 5,
) -> list[tuple[str, int]]:
    """
    Return pages with >= threshold incoming links, sorted by link count.
    These are the conceptual centers of the vault.
    """
    hubs = [
        (stem, len(graph.backward.get(stem, set())))
        for stem in pages
        if len(graph.backward.get(stem, set())) >= threshold
    ]
    return sorted(hubs, key=lambda x: x[1], reverse=True)
```

Emit a `_views/hubs.md` view alongside `index.md`. This is your map of the most important concepts.

---

## 9. GoodNotes Pipeline Specifics

The GoodNotes source is currently commented out in config. It's the highest-value input (your actual course notes) and should be prioritized once Chandra is integrated.

**Expected pipeline:**

```
GoodNotes → PDF export → Google Drive sync → ~/Google Drive/My Drive/Goodnotes/
    → watchdog detects new/changed PDFs
    → force_parse_lane: chandra
    → Chandra OCR (85% benchmark)
    → raw/goodnotes/<course>/<filename>.md
    → triage (Gemma) — most GoodNotes pages are worthwhile, so this is mostly a quality filter
    → compilation agent
```

**Challenges specific to GoodNotes:**

- Handwritten math: Chandra handles this better than generic OCR but expect ~15% error rate on dense equations. The `⚠️` marker in your system prompt will surface these.
- Page ordering: GoodNotes PDFs sometimes have non-sequential page numbering. The `page_number` field in `ParseBlock` tracks this — verify it's populated correctly for multi-page PDFs.
- Diagrams: Non-text content (circuit diagrams, graphs, flowcharts) won't OCR to anything useful. The compilation agent should skip or stub these with `[diagram — see source PDF]` rather than hallucinating content.
- Mixed handwriting + typed text: Some GoodNotes files have both. Chandra should handle this but verify the `confidence` score threshold (currently `0.7` in config) isn't too aggressive for handwritten-only pages which will naturally score lower.

---

## 10. Chat With Your Second Brain — MCP Session Design

The MCP server is the right interface. With it connected to Claude Desktop, the interaction model is:

```
You: "What do I know about martingales and how does it connect to my quant prep?"

Claude:
  1. read_index() → understands vault shape
  2. search_wiki("martingales") → finds relevant pages
  3. find_related("martingales", depth=2) → surfaces connected concepts
  4. read_page("martingales") → reads the concept page
  5. read_page("quant-finance-prep") → reads prep context
  6. Synthesizes across both, aware of your actual notes
```

**For this to work well, the index must be loaded first on every session.** Consider making `read_index` the first tool call in any second-brain session. You can encode this as a Claude Desktop project instruction:

```
When working with my second brain, always start by calling read_index() 
to understand the current state of the vault before answering questions 
or making recommendations.
```

**Context window management across long sessions:**

Unlike ChatGPT's KV cache exhaustion problem, Claude's context summarization (with Code Execution enabled) compresses earlier turns while keeping recent context intact. For second-brain sessions the semantic gist of earlier turns (what topics you explored, what connections you found) survives summarization even if exact tool outputs don't. This is why the switch from ChatGPT is beneficial for this use case specifically.

**The `find_related` depth parameter matters:**

- `depth=1` → direct wikilinks only (fast, precise)
- `depth=2` → links of links (surfaces non-obvious connections)
- `depth=3+` → usually too noisy, degrades to "everything is related to everything"

Default of `depth=2` in your current implementation is correct.

---

## 11. Priority Roadmap

Ordered by impact-to-effort ratio:

### Immediate (bugs, not features)

1. **Fix `rglob` in `discover_all_pages`** — one line, fixes silent subdomain miss
2. **Add token budget guard to `_run_agent`** — prevents runaway API costs
3. **Add termination instruction to `COMPILATION_SYSTEM_PROMPT`** — prevents agent drift

### Short Term

4. **Heuristic pre-filter** on raw sources before any LLM call
5. **Triage module** with Gemma 4 — biggest cost lever
6. **`inbox/` folder + weekly digest generation** in `rebuild_structure`
7. **`get_sources_summary` MCP tool** — prevents token bloat in chat sessions

### Medium Term

8. **Enable GoodNotes source** in config once Chandra integration is verified
9. **Hub detection** in `rebuild_structure` → `_views/hubs.md`
10. **Temporal annotation policy** in compilation system prompt
11. **Replace `applied/`** with `projects/` + `interview-prep/` + `contexts/`

### Longer Term

12. **`cross-domain/` seeding pass** — one-time agent run to identify concepts that should move there from domain folders
13. **Weekly digest CLI command** — `second-brain digest` that surfaces inbox items, orphans, and gaps in a readable format
14. **Embedding index** alongside keyword search — `nomic-embed-text` is already in config, wire it up for semantic MCP queries

---

## 12. Configuration Notes

Current config is well-structured. Suggested additions:

```yaml
triage:
  enabled: true
  model: gemma3:4b
  ollama_host: http://localhost:11434
  min_word_count: 300          # heuristic pre-filter
  worthwhile_threshold: 0.6    # Gemma confidence floor for "worthwhile"

compilation:
  model: claude-sonnet-4-6-20260401
  batch_size: 10
  max_tokens_per_page: 4000
  token_budget_per_run: 150000  # hard stop for agent loop
  
wiki:
  max_folder_depth: 2
  subdomain_creation_threshold: 10   # pages before subdomain is justified
  hub_detection_threshold: 5         # backlinks before page is a hub
```

---

*This document reflects the state of the codebase as reviewed. The pipeline structure (ingest → raw → compile → rebuild → MCP) is sound. The primary gaps are: triage layer, token budget controls, the rglob bug, and the inbox/review flow. Everything else is refinement.*
