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
from second_brain.ollama.client import OllamaUnavailableError
from second_brain.wiki.slugs import normalize_link_list, normalize_wikilinks, slugify
from second_brain.wiki.structure import (
    _FRONTMATTER_RE,
    CONTENT_DIRS,
    FRONTMATTER_EDGE_FIELDS,
    _parse_frontmatter,
    serialize_page,
    update_frontmatter,
)

PAGE_TYPES = ("concept", "problem", "project", "paper", "insight")


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


def describe_tool_call(tool_name: str, tool_input: dict) -> str:
    """
    Describe a tool call as a short present-tense phrase for the live build readout.

    Parameters
    ----------
    tool_name: str
        Name of the tool the agent called.
    tool_input: dict
        The arguments it passed.

    Returns
    -------
    str
        For example ``"Searching the wiki for 'kalman filter'"``.
    """
    page = Path(str(tool_input.get("path", ""))).stem
    title = tool_input.get("title", "")
    query = tool_input.get("query", tool_input.get("pattern", ""))
    match tool_name:
        case "read_file":
            return f"Reading {page}"
        case "write_page":
            return f"Writing {title}"
        case "edit_file":
            return f"Editing {page}"
        case "set_page_meta":
            return f"Updating {tool_input.get('slug', '')}"
        case "glob_files":
            return "Listing files"
        case "grep_files":
            return f"Searching files for '{query}'"
        case "search_wiki" | "semantic_search":
            return f"Searching the wiki for '{query}'"
        case "read_page":
            return f"Reading {title}"
        case "find_related":
            return f"Finding pages related to {title}"
        case "prerequisite_closure":
            return f"Tracing what {title} builds on"
        case "dependents":
            return f"Finding pages that build on {title}"
        case "list_gaps":
            return "Checking topics not written yet"
        case "list_domains":
            return "Checking the wiki's domains"
        case _:
            return "Working"


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
        except OllamaUnavailableError:
            # A build must not continue on degraded search.
            raise
        except Exception as e:
            return f"Error: {e}"

    @property
    def changes(self) -> list[dict]:
        return self._changes

    def _record(self, action: str, rel_path: str) -> None:
        self._changes.append({"action": action, "path": rel_path})

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
        if page_type not in PAGE_TYPES:
            return f"Error: type must be one of {', '.join(PAGE_TYPES)}, got '{page_type}'"
        if not title:
            return "Error: a non-empty title is required"
        slug = slugify(title)
        if not slug:
            return f"Error: title {title!r} has no slug-able characters"

        rel_path = f"{page_type}s/{slug}.md"
        if self._resolve(rel_path).exists():
            return (
                f"Error: {rel_path} already exists. Edit its body with edit_file "
                "rather than recreating it."
            )
        # Stems are unique across all content folders (links resolve by stem
        # alone), so a new page may not reuse another folder's name.
        taken = self._content_page_path(slug)
        if taken is not None:
            return (
                f"Error: the name '{slug}' is taken by {taken}. Pick a distinct "
                "title — for a paper, add the author or year."
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
            "authors",
            "year",
        ):
            value = args.get(key)
            if not value:
                continue
            if key in FRONTMATTER_EDGE_FIELDS and isinstance(value, list):
                value = normalize_link_list(value)
                if not value:
                    continue
            frontmatter[key] = value
        if self._sources:
            frontmatter["sources"] = [f"raw/{source}" for source in self._sources]

        body = normalize_wikilinks(args.get("body", ""))
        return self._write(rel_path, serialize_page(frontmatter, body))

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
        changes: dict = {}
        for key, value in args.items():
            if key in managed or value is None:
                continue
            if key in FRONTMATTER_EDGE_FIELDS and isinstance(value, list):
                value = normalize_link_list(value)
            changes[key] = value
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

        content = self._normalize_page_links(rel_path, content.replace(old, new, 1))
        path.write_text(content, encoding="utf-8")
        self._record("updated", rel_path)
        return f"Edited {rel_path}"

    def _normalize_page_links(self, rel_path: str, content: str) -> str:
        """Canonicalize body wikilinks in the body of a page, leaving frontmatter untouched"""
        if Path(rel_path).parts[0] not in CONTENT_DIRS:
            return content
        match = _FRONTMATTER_RE.match(content)
        if not match:
            return normalize_wikilinks(content)
        return content[: match.end()] + normalize_wikilinks(content[match.end() :])

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
        for content_dir in (*CONTENT_DIRS, "_meta"):
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
