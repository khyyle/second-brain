"""
The compilation agent's interface, including its prompts, tool schemas, the sandboxed
executor that runs those tools, and the conversation compaction the agent loop
relies on.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from second_brain.compilation.structure import CONTENT_DIRS

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
- The wiki is flat: write each page directly in its content folder
  (e.g. concepts/gradient-descent.md), never in a subfolder. Group by topic
  with frontmatter `domains`, not directories.
- File names use kebab-case (e.g., gradient-descent.md)
- Use [[wikilinks]] for cross-references; link by bare page stem only
  (e.g. [[gradient-descent]]), never folder-prefixed ([[concepts/...]]) or
  with a .md suffix
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
    Build the per-run instructions for the agent.

    The source documents themselves are presented separately (see
    :func:`build_source_block`); this is the instruction preamble that names
    them and lays out the steps.

    Parameters:
    -----------
    new_sources: list[str]
        Relative paths of raw source documents to compile.
    wiki_dir: Path
        Root directory of the wiki (used to check for schema).

    Returns:
    --------
    str
        Formatted instruction string for the compilation agent.
    """
    source_list = "\n".join(f"- raw/{s}" for s in new_sources)

    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    schema_note = ""
    if schema_path.exists():
        schema_note = "\nThe schema file is at: _meta/topic_schema.yaml — read it first.\n"

    return f"""\
Compile these source documents into the wiki (paths under raw/, full text provided below):

{source_list}

{schema_note}
Instructions:
1. Read _meta/topic_schema.yaml
2. Search existing wiki pages for related content
3. Create or update wiki pages from the sources provided below
4. Ensure all pages have proper frontmatter, [[wikilinks]], and source citations

Re-read any source section with read_file/grep_files (raw/ paths) only if you
need to relocate a specific passage; their full text is already provided."""


def build_source_block(sources: list[str], raw_dir: Path) -> str:
    """Concatenate the raw source documents for inline presentation.

    Each source is read in full and labeled with its raw/ path so the agent
    can attribute and cite it. Presenting the source inline (rather than
    making the agent fetch it) lets the orchestrator cache it as a stable
    prefix, so re-sending it each turn is cheap.

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


# Maximum characters returned by a single read. Longer files are paged via the
# read_file offset argument.
_MAX_READ_CHARS = 24_000


def _is_raw_path(path: str) -> bool:
    """Whether an agent-supplied path or glob targets the read-only source tree."""
    return path.startswith("raw/") or path.startswith("../raw/")


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
        """
        self._wiki_dir = wiki_dir
        self._raw_dir = raw_dir
        self._dry_run = dry_run
        self._data_dir = data_dir
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
            elif tool_name == "write_file":
                return self._write(tool_input["path"], tool_input["content"])
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
        # error if nested file creation is attempted structure is already enforced
        # via backlinks and nesting would muddle this structure
        parts = Path(rel_path).parts
        if len(parts) > 2 and parts[0] in CONTENT_DIRS:
            return (
                f"Error: write pages flat as {parts[0]}/<name>.md, not in subfolders. "
                "Group topics with frontmatter 'domains' instead."
            )

        if self._dry_run:
            self._record("created", rel_path)
            return f"[dry-run] Would write {len(content)} chars to {rel_path}"

        path = self._resolve(rel_path)
        action = "updated" if path.exists() else "created"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._record(action, rel_path)
        return f"Wrote {len(content)} chars to {rel_path}"

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
