"""
The compilation agent's interface, including its prompts, tool schemas, the sandboxed
executor that runs those tools, and the conversation compaction the agent loop
relies on.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from second_brain.mcp_server.tools import WikiTools
from second_brain.wiki.structure import (
    CONTENT_DIRS,
    _parse_frontmatter,
    serialize_page,
    update_frontmatter,
)

COMPILATION_SYSTEM_PROMPT = """\
You are a knowledge-base compilation agent. Your job is to read raw parsed \
source documents and compile them into a structured, interlinked wiki.

## Working Directory
You operate inside a wiki directory with this structure:
- _meta/topic_schema.yaml — defines content types, the domain vocabulary, and rules
- concepts/ — what things ARE (theory, definitions, math)
- problems/ — practice problems, exercises, worked examples
- projects/ — things being BUILT (systems, experiments)
- insights/ — distilled knowledge from conversations, lectures
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
   a. Content type (concept / problem / project / insight)
   b. Does a related page exist? → update it
   c. Is this new? → create a new page in the right folder
   d. Is a page too long (>4000 words)? → split it
5. Write or update each page per the Fields and Rules below

## Writing pages
- write_page — create a NEW page. You supply the content fields below; the system
  writes a valid frontmatter block, names and places the file, and records its
  sources. Do not hand-write frontmatter or choose a path.
- set_page_meta — change an existing page's frontmatter fields (domains, tags,
  prerequisites, related, ...) in place, leaving the body untouched.
- edit_file — change an existing page's body prose.

## Fields you choose
write_page assembles the frontmatter from the fields you pass. On every page:
- title: human-readable string
- type: concept | problem | project | insight
- domains: list of broad subject areas (bare kebab strings)
- tags: list of narrow topics (bare kebab strings)

Plus the fields for its type — concept: prerequisites, related · problem:
difficulty, concepts_tested · project: status, concepts_used · insight:
key_takeaways.

prerequisites / related / concepts_tested / concepts_used are lists of bare-stem
[[wikilinks]]; include one even if its page does not exist yet, so it records a
real edge, not loose text.

## Rules
- Use [[wikilinks]] for cross-references; link by bare page stem only
  (e.g. [[gradient-descent]]), never folder-prefixed ([[concepts/...]]) or
  with a .md suffix
- Use LaTeX notation: inline $...$ and display $$...$$
- Cite sources with ^[source-filename.md] notation
- You may create new tags freely
- Domains are broad subject areas (e.g. mathematics, finance, biology). Reuse an
  existing one from the schema when it fits; add a new domain only for a genuinely
  distinct broad area, not a narrow topic (that's a tag or its own page).
- Do NOT rebuild index.md or structural metadata — that runs separately

## Quality Standards
- Pages should be 500-3000 words
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


def build_compilation_prompt(new_sources: list[str]) -> str:
    """
    Build the per-run task message, detailing sources and start sequence.

    Parameters:
    -----------
    new_sources: list[str]
        Relative paths of raw source documents to compile.

    Returns:
    --------
    str
        The per-run task message for the compilation agent.
    """
    # sources are presented separately, see :func:`build_source_block`
    source_list = "\n".join(f"- raw/{s}" for s in new_sources)

    return f"""\
Compile these source documents into the wiki (paths under raw/, full text provided below):

{source_list}

To start:
1. Read _meta/topic_schema.yaml
2. Search existing pages so you update rather than duplicate
3. Create or update pages from the sources

Re-read a source with read_file/grep_files (raw/ paths) only to relocate a
passage; their full text is already provided."""


def build_source_block(sources: list[str], raw_dir: Path) -> str:
    """Concatenate the raw source documents for inline presentation.

    Each source is read in full and labeled with its raw/ path so the agent
    can attribute and cite it.

    Parameters
    ----------
    sources: list[str]
        Relative paths of raw source documents to present.
    raw_dir: Path
        Directory containing the raw source files.

    Returns
    -------
    str
        The labeled, concatenated source text.
    """
    parts: list[str] = []
    for rel in sources:
        path = raw_dir / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            text = f"[source unavailable: {exc}]"
        parts.append(f"=== raw/{rel} ===\n{text}")
    return "\n\n".join(parts)


# Content types the agent may create, each mapping to its `<type>s/` directory.
_PAGE_TYPES = ("concept", "problem", "project", "insight")

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
                    "enum": list(_PAGE_TYPES),
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


# Read-only wiki-exploration tools the agent gets when compilation.explore_tools is on.
# These query the compiled wiki (not raw sources), complementing the file tools above.
EXPLORE_TOOL_NAMES = (
    "search_wiki",
    "semantic_search",
    "find_related",
    "prerequisite_closure",
    "dependents",
    "list_gaps",
    "read_page",
    "list_domains",
)

EXPLORE_TOOLS_GUIDANCE = (
    "You also have read-only wiki-exploration tools: search_wiki, semantic_search, "
    "find_related, prerequisite_closure, dependents, list_gaps, read_page, and "
    "list_domains. Use them to find existing pages to link or update so you avoid "
    "creating duplicates and wire new pages into the existing graph. They reflect "
    "the wiki as it was before this run, so pages you create now will not appear in "
    "their results--you already know what you have written this run."
)


def explore_tool_schemas() -> list[dict]:
    """
    Return Anthropic-format schemas for the wiki-exploration tools.

    Derived from the MCP server's tool registry so the descriptions and parameter
    schemas have a single source of truth shared with the MCP client, rather than
    being duplicated here.

    Returns
    -------
    list[dict]
        ``{name, description, input_schema}`` dicts in ``EXPLORE_TOOL_NAMES`` order.
    """
    import asyncio

    from second_brain.mcp_server.server import mcp

    registered = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schemas: list[dict] = []
    for name in EXPLORE_TOOL_NAMES:
        tool = registered.get(name)
        if tool is not None:
            schemas.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
            )
    return schemas


# Maximum characters returned by a single read. Longer files are paged via the
# read_file offset argument.
_MAX_READ_CHARS = 24_000


def _is_raw_path(path: str) -> bool:
    """Whether an agent-supplied path or glob targets the read-only source tree."""
    return path.startswith("raw/") or path.startswith("../raw/")


def _slugify(title: str) -> str:
    """Reduce a page title to a kebab-case filename stem."""
    cleaned = "".join(c if c.isalnum() or c in " -" else "" for c in title.lower())
    return "-".join(cleaned.split())


class WikiToolExecutor:
    """Runs the agent's filesystem tools, sandboxed to the wiki and source trees.

    Every path is resolved and validated so a tool call cannot read or write
    outside the wiki and raw directories.
    """

    def __init__(
        self,
        wiki_dir: Path,
        raw_dir: Path,
        dry_run: bool = False,
        data_dir: Path | None = None,
        read_tools: WikiTools | None = None,
        sources: list[str] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        wiki_dir: Path
            Root directory of the wiki.
        raw_dir: Path
            Directory containing raw parsed source files.
        dry_run: bool
            If ``True``, record intended writes without touching the filesystem.
        data_dir: Path | None
            Vault root; when set, each page change is appended to the build log
            as it happens so progress is observable mid-run.
        read_tools: WikiTools | None
            When provided, the agent can also call the wiki-exploration tools in
            ``EXPLORE_TOOL_NAMES``, dispatched to this instance's methods.
        sources: list[str] | None
            Raw-relative paths of the build unit being compiled. ``write_page``
            stamps these as a page's provenance, so the agent never supplies it.
        """
        self._wiki_dir = wiki_dir
        self._raw_dir = raw_dir
        self._dry_run = dry_run
        self._data_dir = data_dir
        self._read_tools = read_tools
        self._sources = sources or []
        self._changes: list[dict] = []

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """
        Dispatch a tool call from the agent.

        Parameters
        ----------
        tool_name: str
            Name of the tool (e.g., ``"read_file"``).
        tool_input: dict
            Arguments forwarded from the LLM tool-use block.

        Returns
        -------
        str
            Human-readable result or error message.
        """
        try:
            if tool_name == "read_file":
                return self._read(tool_input["path"], tool_input.get("offset", 0))
            elif tool_name == "write_page":
                return self._write_page(tool_input)
            elif tool_name == "set_page_meta":
                return self._set_page_meta(tool_input)
            elif tool_name == "edit_file":
                return self._edit(
                    tool_input["path"],
                    tool_input["old_string"],
                    tool_input["new_string"],
                )
            elif tool_name == "glob_files":
                return self._glob(tool_input["pattern"])
            elif tool_name == "grep_files":
                return self._grep(
                    tool_input["pattern"],
                    tool_input.get("glob", "**/*.md"),
                )
            elif self._read_tools is not None and tool_name in EXPLORE_TOOL_NAMES:
                # exploration tools share the MCP read methods, dispatched by name
                return getattr(self._read_tools, tool_name)(**tool_input)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Error: {e}"

    @property
    def changes(self) -> list[dict]:
        return self._changes

    def _record(self, action: str, rel_path: str) -> None:
        """Record a page change and append it to the build log immediately."""
        self._changes.append({"action": action, "path": rel_path})
        if self._data_dir is not None and not self._dry_run:
            from second_brain.build_log import append_build_actions

            append_build_actions(self._data_dir, [{"action": action, "path": rel_path}])

    def _resolve(self, rel_path: str) -> Path:
        """
        Resolve a relative path to the wiki or raw directory.

        Paths under ``raw/`` resolve against the raw directory; all others
        resolve against the wiki directory. Both are checked against
        directory-traversal outside their root.

        Parameters
        ----------
        rel_path: str
            Relative path as provided by the LLM agent.

        Returns
        -------
        Path
            Resolved absolute path.

        Raises
        ------
        PermissionError
            If the resolved path escapes its sandbox.
        """
        if _is_raw_path(rel_path):
            clean = rel_path.replace("../raw/", "").replace("raw/", "")
            resolved = (self._raw_dir / clean).resolve()
            if not str(resolved).startswith(str(self._raw_dir.resolve())):
                raise PermissionError(f"Path escapes raw directory: {rel_path}")
            return resolved

        resolved = (self._wiki_dir / rel_path).resolve()
        if not str(resolved).startswith(str(self._wiki_dir.resolve())):
            raise PermissionError(f"Path escapes wiki directory: {rel_path}")
        return resolved

    def _read(self, rel_path: str, offset: int = 0) -> str:
        """Read a file, returning at most ``_MAX_READ_CHARS`` from ``offset``.

        When more content remains, the trailing note carries the offset to pass
        on the next call to continue reading.
        """
        path = self._resolve(rel_path)
        if not path.exists():
            return f"File not found: {rel_path}"
        text = path.read_text(encoding="utf-8")
        total = len(text)
        offset = max(offset, 0)
        if offset == 0 and total <= _MAX_READ_CHARS:
            return text

        window = text[offset : offset + _MAX_READ_CHARS]
        end = offset + len(window)
        if end < total:
            note = f"read again with offset={end} for the next {total - end} chars"
        else:
            note = "end of file"
        return f"{window}\n\n[chars {offset}-{end} of {total} — {note}]"

    def _write(self, rel_path: str, content: str) -> str:
        """
        Write content to a file, creating parents as needed.

        Parameters
        ----------
        rel_path: str
            Relative path from wiki root.
        content: str
            Full file content to write.

        Returns
        -------
        str
            Confirmation message with character count.
        """
        if self._dry_run:
            self._record("created", rel_path)
            return f"[dry-run] Would write {len(content)} chars to {rel_path}"

        path = self._resolve(rel_path)
        action = "updated" if path.exists() else "created"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._record(action, rel_path)
        return f"Wrote {len(content)} chars to {rel_path}"

    def _write_page(self, args: dict) -> str:
        """
        Create a new wiki page from structured fields.

        Serializes a guaranteed-valid frontmatter block, derives the path from
        the content type and title, and stamps the build unit's sources, so the
        agent supplies only the content. Refuses to overwrite an existing page.

        Parameters
        ----------
        args: dict
            Tool arguments: ``type``, ``title``, ``body``, and the optional
            judgment fields (``domains``, ``tags``, ``prerequisites``, ...).

        Returns
        -------
        str
            Confirmation with the written path, or an error message.
        """
        page_type = args.get("type", "")
        title = (args.get("title") or "").strip()
        if page_type not in _PAGE_TYPES:
            return f"Error: type must be one of {', '.join(_PAGE_TYPES)}, got '{page_type}'"
        if not title:
            return "Error: a non-empty title is required"
        slug = _slugify(title)
        if not slug:
            return f"Error: title {title!r} has no slug-able characters"

        rel_path = f"{page_type}s/{slug}.md"
        if self._resolve(rel_path).exists():
            return (
                f"Error: {rel_path} already exists. Edit its body with edit_file "
                "rather than recreating it."
            )

        frontmatter: dict = {"title": title, "type": page_type}
        for key in (
            "domains",
            "tags",
            "prerequisites",
            "related",
            "concepts_tested",
            "concepts_used",
            "key_takeaways",
            "difficulty",
            "status",
        ):
            value = args.get(key)
            if value:
                frontmatter[key] = value
        if self._sources:
            frontmatter["sources"] = [f"raw/{source}" for source in self._sources]

        return self._write(rel_path, serialize_page(frontmatter, args.get("body", "")))

    def _content_page_path(self, slug: str) -> str | None:
        """Return the relative path of the content page with this stem, if any."""
        for content_dir in CONTENT_DIRS:
            rel = f"{content_dir}/{slug}.md"
            if self._resolve(rel).exists():
                return rel
        return None

    def _set_page_meta(self, args: dict) -> str:
        """
        Update an existing page's frontmatter fields, leaving the body untouched.

        Provenance, type, and title are system-managed, so they are ignored if
        passed; the remaining fields are merged into the page's frontmatter.

        Parameters
        ----------
        args: dict
            Tool arguments: ``slug`` plus the frontmatter fields to set.

        Returns
        -------
        str
            Confirmation or an error message.
        """
        slug = (args.get("slug") or "").strip()
        if not slug:
            return "Error: a slug is required"
        rel_path = self._content_page_path(slug)
        if rel_path is None:
            return f"Error: no page found with slug '{slug}'"

        managed = {"slug", "sources", "type", "title"}
        changes = {
            key: value for key, value in args.items() if key not in managed and value is not None
        }
        if not changes:
            return "Error: no frontmatter fields given to update"

        path = self._resolve(rel_path)
        updated = update_frontmatter(path.read_text(encoding="utf-8"), changes)
        if updated is None:
            return f"Error: {rel_path} has no frontmatter block to update"
        if self._dry_run:
            self._record("updated", rel_path)
            return f"[dry-run] Would update frontmatter of {rel_path}"
        path.write_text(updated, encoding="utf-8")
        self._record("updated", rel_path)
        return f"Updated frontmatter of {rel_path}"

    def finalize_provenance(self) -> None:
        """
        Stamp the build unit's sources onto every page touched this run.

        A page created via ``write_page`` already carries them; a page updated
        via ``edit_file`` or ``set_page_meta`` gets them merged in (union,
        order-preserving), so a page's sources accumulate every source it was
        compiled from and never regress. No-op without a build unit or in
        dry-run.
        """
        if self._dry_run or not self._sources:
            return
        unit_sources = [f"raw/{source}" for source in self._sources]
        for rel_path in {change["path"] for change in self._changes}:
            if Path(rel_path).parts[0] not in CONTENT_DIRS:
                continue
            path = self._resolve(rel_path)
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            existing = _parse_frontmatter(content).get("sources") or []
            if isinstance(existing, str):
                existing = [existing]
            merged = list(existing)
            for source in unit_sources:
                if source not in merged:
                    merged.append(source)
            if merged == existing:
                continue
            rewritten = update_frontmatter(content, {"sources": merged})
            if rewritten is not None:
                path.write_text(rewritten, encoding="utf-8")

    def _edit(self, rel_path: str, old: str, new: str) -> str:
        """
        Replace a unique substring in a file.

        Parameters
        ----------
        rel_path: str
            Relative path from wiki root.
        old: str
            Exact string to find (must appear exactly once).
        new: str
            Replacement string.

        Returns
        -------
        str
            Confirmation or error message.
        """
        path = self._resolve(rel_path)
        if not path.exists():
            return f"File not found: {rel_path}"

        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            return f"old_string not found in {rel_path}"
        if count > 1:
            return f"old_string appears {count} times in {rel_path} — must be unique"

        if self._dry_run:
            self._record("updated", rel_path)
            return f"[dry-run] Would edit {rel_path}"

        content = content.replace(old, new, 1)
        path.write_text(content, encoding="utf-8")
        self._record("updated", rel_path)
        return f"Edited {rel_path}"

    def _search_root(self, glob: str) -> tuple[Path, str, str]:
        """Resolve a glob's prefix to a search root.

        A glob under ``raw/`` searches the read-only source tree; anything else
        searches the wiki. Returns the root directory, the glob with any
        ``raw/`` prefix stripped, and the prefix to prepend to reported paths.
        """
        if _is_raw_path(glob):
            stripped = glob.replace("../raw/", "").replace("raw/", "", 1)
            return self._raw_dir, stripped, "raw/"
        return self._wiki_dir, glob, ""

    def _glob(self, pattern: str) -> str:
        """
        Find files matching a glob pattern.

        Searches the wiki content directories, or the raw source tree when the
        pattern is under ``raw/``.

        Parameters
        ----------
        pattern: str
            Glob pattern (e.g., ``"concepts/*.md"`` or ``"raw/chatgpt/*.md"``).

        Returns
        -------
        str
            Newline-separated relative paths, or ``"No matches found"``.
        """
        basename = pattern.split("/")[-1]
        if _is_raw_path(pattern):
            matches = [
                f"raw/{f.relative_to(self._raw_dir)}"
                for f in self._raw_dir.rglob("*")
                if f.is_file() and fnmatch.fnmatch(f.name, basename)
            ]
            return "\n".join(sorted(matches)) if matches else "No matches found"

        matches = []
        for content_dir in ("concepts", "problems", "projects", "insights", "_meta"):
            dir_path = self._wiki_dir / content_dir
            if not dir_path.exists():
                continue
            for f in dir_path.rglob("*"):
                if f.is_file() and fnmatch.fnmatch(f.name, basename):
                    matches.append(str(f.relative_to(self._wiki_dir)))
        return "\n".join(sorted(matches)) if matches else "No matches found"

    def _grep(self, pattern: str, file_glob: str) -> str:
        """
        Search file contents for a regex pattern.

        Searches the wiki by default, or the raw source tree when ``file_glob``
        is under ``raw/``. Results are capped at 100 matches to keep tool output
        from consuming the context window.

        Parameters
        ----------
        pattern: str
            Regex pattern (matched case-insensitively).
        file_glob: str
            Glob restricting which files to search (e.g. ``"concepts/*.md"`` or
            ``"raw/chatgpt/some-chat.md"``).

        Returns
        -------
        str
            Newline-separated ``path:line: content`` matches, or
            ``"No matches found"``.
        """
        compiled = re.compile(pattern, re.IGNORECASE)
        root, glob, prefix = self._search_root(file_glob)
        results: list[str] = []
        for match_file in root.rglob(glob.lstrip("*").lstrip("/")):
            if not match_file.is_file():
                continue
            try:
                content = match_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    rel = match_file.relative_to(root)
                    results.append(f"{prefix}{rel}:{i}: {line.strip()}")
            if len(results) > 100:
                results.append("... (truncated)")
                break
        return "\n".join(results) if results else "No matches found"


# Total chars of on-demand raw-source reads kept resident across turns. Bounds
# how much re-read source content can accumulate, regardless of source size.
_PROTECTED_SOURCE_CHARS = 48_000


def _protected_source_read_ids(messages: list[dict], max_chars: int) -> set[str]:
    """Tool-use ids of recent raw-source reads to keep resident, within a budget.

    The latest read of each distinct ``(path, offset)`` page is a candidate;
    candidates are kept newest-first until their combined size would exceed
    ``max_chars``, so an on-demand re-read can never pin an unbounded amount of
    source content for the rest of the run.
    """
    latest_by_page: dict[tuple[str, int], tuple[int, str]] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if getattr(block, "type", None) != "tool_use" or block.name != "read_file":
                continue
            args = block.input or {}
            path = args.get("path", "")
            if _is_raw_path(path):
                latest_by_page[(path, args.get("offset", 0))] = (index, block.id)

    result_sizes: dict[str, int] = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                body = block.get("content")
                if isinstance(body, str):
                    result_sizes[block.get("tool_use_id")] = len(body)

    protected: set[str] = set()
    used = 0
    for _index, tool_use_id in sorted(latest_by_page.values(), reverse=True):
        size = result_sizes.get(tool_use_id, 0)
        if protected and used + size > max_chars:
            break
        protected.add(tool_use_id)
        used += size
    return protected


def compact_history(messages: list[dict], keep_last: int = 2, max_chars: int = 600) -> None:
    """Shrink large tool outputs in older turns to keep each request small.

    The whole conversation is re-sent every turn, so an early full-file read
    would inflate every later request. The most recent ``keep_last`` user turns
    are left intact, as are the newest raw-source reads up to a character budget
    so the agent retains recently-fetched material; other oversized tool results
    are replaced with a placeholder and can be re-read on demand.

    Parameters
    ----------
    messages: list[dict]
        The running conversation, mutated in place.
    keep_last: int
        Number of most-recent user turns to leave untouched.
    max_chars: int
        Tool-result size above which an old result is collapsed.
    """
    placeholder = "[earlier output omitted to save context — re-read if needed]"
    protected_ids = _protected_source_read_ids(messages, _PROTECTED_SOURCE_CHARS)
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    protected_turns = set(user_indices[-keep_last:])
    for i, message in enumerate(messages):
        if message.get("role") != "user" or i in protected_turns:
            continue
        content = message.get("content")
        if not isinstance(content, list):  # a plain-string message has no blocks
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") not in protected_ids
                and isinstance(block.get("content"), str)
                and len(block["content"]) > max_chars
            ):
                block["content"] = placeholder
