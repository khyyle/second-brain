from second_brain.compilation.agent import PAGE_TYPES

# Tool definitions in Anthropic's tool-use schema format.
# These are passed to the API as the `tools` parameter and define
# what filesystem operations the agent can perform during compilation.
WIKI_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a file in the wiki, or a source under raw/. Long files return "
            "one page at a time; the result says the offset to pass to continue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path from wiki root (e.g., concepts/gradient-descent.md), "
                        "or a raw/ source path (e.g., raw/chatgpt/some-chat.md)"
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Character offset to start reading from, for paging through a "
                        "long file (default 0)"
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace a specific string in a file. The old_string must appear exactly once."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from wiki root",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact string to find and replace",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement string",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_page",
        "description": (
            "Create a NEW wiki page from structured fields. The system serializes a "
            "guaranteed-valid frontmatter block, places the file by type and title, "
            "and records its sources for you -- so do NOT write a frontmatter block "
            "or choose a path. To change an existing page, use edit_file on its body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(PAGE_TYPES),
                    "description": "Content type; also selects the folder.",
                },
                "title": {"type": "string", "description": "Human-readable page title."},
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Broad subject areas, as bare kebab strings.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Narrow topic tags, as bare kebab strings.",
                },
                "prerequisites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "concept: prerequisite [[wikilinks]].",
                },
                "related": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "concept: related [[wikilinks]].",
                },
                "concepts_tested": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "problem: tested-concept [[wikilinks]].",
                },
                "concepts_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "project: used-concept [[wikilinks]].",
                },
                "key_takeaways": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "insight: key takeaway lines.",
                },
                "difficulty": {"type": "string", "description": "problem: difficulty."},
                "status": {"type": "string", "description": "project: status."},
                "authors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source authors, when the source names them.",
                },
                "year": {"type": "integer", "description": "Source publication year."},
                "body": {
                    "type": "string",
                    "description": "Markdown body only (no frontmatter block).",
                },
            },
            "required": ["type", "title", "body"],
        },
    },
    {
        "name": "set_page_meta",
        "description": (
            "Update an existing page's frontmatter fields in place (domains, tags, "
            "prerequisites, related, ...), leaving the body untouched. Use this to "
            "adjust a page's metadata or links; use edit_file for its body. A page's "
            "type, title, and sources are system-managed and cannot be set here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Page stem to update (filename without folder or .md).",
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Broad subject areas (replaces the whole list).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Narrow topic tags (replaces the whole list).",
                },
                "prerequisites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "concept: prerequisite [[wikilinks]].",
                },
                "related": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "concept: related [[wikilinks]].",
                },
                "concepts_tested": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "problem: tested-concept [[wikilinks]].",
                },
                "concepts_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "project: used-concept [[wikilinks]].",
                },
                "key_takeaways": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "insight: key takeaway lines.",
                },
                "difficulty": {"type": "string", "description": "problem: difficulty."},
                "status": {"type": "string", "description": "project: status."},
                "authors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source authors, when the source names them.",
                },
                "year": {"type": "integer", "description": "Source publication year."},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "glob_files",
        "description": (
            "Find files matching a glob pattern in the wiki, or under raw/ to "
            "list source documents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern (e.g., 'concepts/*.md', '**/*gradient*', 'raw/chatgpt/*.md')"
                    ),
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_files",
        "description": (
            "Search file contents for a pattern in the wiki, or in a raw/ source "
            "to locate sections of a long document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Optional glob to restrict which files are searched (default: "
                        "'**/*.md'); use a 'raw/...' glob to search source documents"
                    ),
                },
            },
            "required": ["pattern"],
        },
    },
]


COMPILATION_SYSTEM_PROMPT = """\
You are a knowledge-base compilation agent. Your job is to read raw parsed \
source documents and compile them into a structured, interlinked wiki.

## Working Directory
You operate inside a wiki directory with this structure:
- _meta/topic_schema.yaml — defines content types, the domain vocabulary, and rules
- concepts/ — what things ARE: definitions, theory, derivations. The
  axiomatic layer every other bucket links against.
- problems/ — worked examples, exercises, and drills that apply concepts.
- projects/ — things the USER built or ran: their systems, experiments,
  code. A system described in a paper is not a project; it belongs on that
  paper's page.
- papers/ — one page per academic paper: its argument (problem,
  contribution, key results, limitations). The methods a paper introduces
  or uses live in concepts/.
- insights/ — realizations that span sources or come from the user's own
  thinking. A single paper's takeaways belong on its papers/ page.
- raw/ — the ingested source documents to read (read-only). Reference
  them with their full `raw/...` path, exactly as listed in the task.

## Your Process
1. Read _meta/topic_schema.yaml FIRST to understand allowed structure
2. The source documents to compile are provided in full in the task message.
   Use read_file/grep_files on their raw/ paths only to re-locate a specific
   passage or to read the companion .json sidecar; you do not need to read the
   sources again to see their content.
3. Search existing wiki pages for related content
4. For each piece of knowledge, decide:
   a. Content type (concept / problem / project / paper / insight)
   b. Does a related page exist? → update it
   c. Is this new? → create a new page in the right folder
   d. Is a page bloated by separable ideas? → split it into linked pages
5. Write or update each page per the Fields and Rules below

## Writing pages
- write_page — create a NEW page. You supply the content fields below; the system
  writes a valid frontmatter block, names and places the file, and records its
  sources. Do not hand-write frontmatter or choose a path.
- set_page_meta — change an existing page's frontmatter fields (domains, tags,
  prerequisites, related, ...) in place, leaving the body untouched.
- edit_file — change an existing page's body prose.

## Naming
Identifiers (wikilink targets, domains, tags) are lowercase ASCII kebab-case.
A page is identified by its title in that form: the page titled "L'Hôpital's
Rule" is linked as [[lhopitals-rule]]. Titles themselves stay human-readable.
Write identifiers in kebab-case; the system canonicalizes case, punctuation,
accents, and stray dashes, so an imperfect one still resolves.

## Fields you choose
write_page assembles the frontmatter from the fields you pass. On every page:
- title: human-readable string
- type: concept | problem | project | paper | insight
- domains: list of broad subject areas
- tags: list of narrow topics

Plus the fields specific to its type:
- concept: prerequisites, related
- problem: difficulty, concepts_tested
- project: status, concepts_used
- paper: authors, year, related
- insight: key_takeaways

The relationship fields (prerequisites, related, concepts_tested,
concepts_used) are lists of [[wikilinks]].

## Rules
- Every academic paper gets a papers/ page: the problem, why prior
  approaches fall short, the contribution as links to the concepts it
  introduces, key results with numbers, and limitations. Derivations go in
  concept pages. This holds even when the wiki already covers the ideas —
  extend the covering pages with the paper's findings so they cite it; a
  paper is never skipped as already covered. If the title collides with an
  existing page name, disambiguate with author or year — the title "NICE
  (Dinh 2015)" gives the identifier [[nice-dinh-2015]].
- Cross-reference with [[wikilinks]]. Add a display label when the prose
  wants one, as in [[dot-product|dot product]].
- A linked concept does not need to have a page yet, whether the link sits in
  prose or in a relationship field. The dangling link records the concept as
  referenced-but-not-written, and it resolves on its own once that page is
  built. Only link concepts substantial enough to merit a page of their own,
  and leave incidental phrases as plain text.
- Keep each page complete in its own scope. Write out this page's own
  derivations fully. When a step rests on an outside concept, link it in the
  prose instead of re-deriving it (a statistical test cites
  [[central-limit-theorem]], it does not re-prove it). A link may accompany a
  step, never stand in for one.
- Inside code blocks and math, [[ ]] is literal bracket syntax, never a link.
  Matrices and array literals are safe to write as usual; a link belongs in
  the surrounding text.
- Use LaTeX notation: inline $...$ and display $$...$$
- Cite sources with ^[source-filename.md] notation
- You may create new tags freely
- Domains are broad subject areas (e.g. mathematics, finance, biology). Reuse an
  existing one from the schema when it fits; add a new domain only for a genuinely
  distinct broad area, not a narrow topic (that's a tag or its own page).
- Do NOT rebuild index.md or structural metadata--it runs separately

## Quality Standards
- Knowledge stays dense: write derivations and reasoning out in full, and
  never summarize them away. Length follows content, most pages land
  between 500 and 3000 words, but a genuinely atomic derivation/explanation may run
  long. Split only when a page bundles separable ideas, never for length
  alone.
- Synthesize across sources, don't just copy
- Resolve contradictions between sources when possible
- Preserve detail from the source material (e.g. mathematical precision)

## Termination
Once you have processed every source document in the provided list and
written or updated all relevant wiki pages, stop immediately. Do not
continue editing pages that were not directly affected by the new
sources, and do not keep "improving" existing pages beyond what the new
sources warrant. When done, briefly summarize what you created or
updated and end your turn.
"""

EXPLORE_TOOLS_GUIDANCE = (
    "You also have read-only wiki-exploration tools: search_wiki, semantic_search, "
    "find_related, prerequisite_closure, dependents, list_gaps, read_page, and "
    "list_domains. Use them to find existing pages to link or update so you avoid "
    "creating duplicates and wire new pages into the existing graph. They reflect "
    "the wiki as it was before this run, so pages you create now will not appear in "
    "their results--you already know what you have written this run."
)
