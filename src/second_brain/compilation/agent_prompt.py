"""Compilation agent prompts — system prompt and per-run context construction."""

from __future__ import annotations

from pathlib import Path

COMPILATION_SYSTEM_PROMPT = """\
You are a knowledge-base compilation agent. Your job is to read raw parsed \
source documents and compile them into a structured, interlinked wiki.

## Working Directory
You operate inside a wiki directory with this structure:
- _meta/topic_schema.yaml — defines content types, domains, tags, and rules
- _meta/schema_proposals.yaml — where you propose new domains
- concepts/ — what things ARE (theory, definitions, math)
- problems/ — practice problems, exercises, worked examples
- projects/ — things being BUILT (systems, experiments)
- insights/ — distilled knowledge from conversations, lectures
- raw/ — the ingested source documents to read (read-only). Reference
  them with their full `raw/...` path, exactly as listed in the task.

## Your Process
1. Read _meta/topic_schema.yaml FIRST to understand allowed structure
2. Read each new source document (both .md and .json)
3. Search existing wiki pages for related content
4. For each piece of knowledge, decide:
   a. Content type (concept / problem / project / insight)
   b. Does a related page exist? → update it
   c. Is this new? → create a new page in the right folder
   d. Is a page too long (>4000 words)? → split it
5. Write pages with proper YAML frontmatter, [[wikilinks]], LaTeX math, source citations
6. Assign domains and tags in frontmatter (pages can have multiple domains)

## Page Format
Every page MUST have YAML frontmatter with at minimum:
- title: Human-readable title
- type: concept | problem | project | insight
- domains: list of domain strings
- tags: list of tag strings
- sources: list of source document filenames

## Rules
- File names use kebab-case (e.g., gradient-descent.md)
- Use [[wikilinks]] for cross-references between pages
- Use LaTeX notation: inline $...$ and display $$...$$
- Cite sources with ^[source-filename.md] notation
- You may create new tags freely
- You must NOT create new domains — propose them via _meta/schema_proposals.yaml
- Do NOT rebuild index.md, backlinks, or structural metadata — that runs separately

## Quality Standards
- Pages should be 500-3000 words
- Synthesize across sources, don't just copy
- Resolve contradictions between sources when possible
- Preserve mathematical precision from source material

## Termination
Once you have processed every source document in the provided list and
written or updated all relevant wiki pages, stop immediately. Do not
continue editing pages that were not directly affected by the new
sources, and do not keep "improving" existing pages beyond what the new
sources warrant. When done, briefly summarize what you created or
updated and end your turn.
"""


def build_compilation_prompt(
    new_sources: list[str],
    wiki_dir: Path,
) -> str:
    """
    Build the per-run prompt listing new sources for the agent.

    Parameters:
    -----------
    new_sources: list[str]
        Relative paths of raw source documents to compile.
    wiki_dir: Path
        Root directory of the wiki (used to check for schema).

    Returns:
    --------
    str
        Formatted prompt string for the compilation agent.
    """
    source_list = "\n".join(f"- raw/{s}" for s in new_sources)

    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    schema_note = ""
    if schema_path.exists():
        schema_note = "\nThe schema file is at: _meta/topic_schema.yaml — read it first.\n"

    return f"""\
New source documents to compile into the wiki (paths are under raw/):

{source_list}

{schema_note}
Instructions:
1. Read _meta/topic_schema.yaml
2. For each source above, read it with its exact raw/ path. A companion
   .json with the same path (.json extension) may also exist — read it too.
3. Search existing wiki pages for related content
4. Create or update wiki pages as appropriate
5. Ensure all pages have proper frontmatter, [[wikilinks]], and source citations

Begin by reading the schema, then process each source document."""


# Tool definitions in Anthropic's tool-use schema format.
# These are passed to the API as the `tools` parameter and define
# what filesystem operations the agent can perform during compilation.
WIKI_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the wiki directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path from wiki root (e.g., concepts/gradient-descent.md)"
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file in the wiki directory. Creates parent directories if needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from wiki root",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
            },
            "required": ["path", "content"],
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
        "name": "glob_files",
        "description": "Find files matching a glob pattern in the wiki directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g., 'concepts/*.md', '**/*gradient*')",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_files",
        "description": "Search file contents for a pattern in the wiki directory.",
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
                        "Optional glob to restrict which files are searched (default: '**/*.md')"
                    ),
                },
            },
            "required": ["pattern"],
        },
    },
]
