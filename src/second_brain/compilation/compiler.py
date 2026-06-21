"""Compilation orchestrator — agentic content synthesis then deterministic rebuild."""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import anthropic

from second_brain.compilation.agent_prompt import (
    COMPILATION_SYSTEM_PROMPT,
    WIKI_TOOLS,
    build_compilation_prompt,
)
from second_brain.compilation.structure import rebuild_structure
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest

logger = logging.getLogger(__name__)


class MissingAPIKeyError(RuntimeError):
    """
    Raised when a build is attempted without an Anthropic API key.
    """

# Cap a single file read so one large source can't blow the per-minute
# input-token budget (~6k tokens). The agent can grep for specifics.
_MAX_READ_CHARS = 24_000

# Holding folder under raw/ for skipped sources: kept out of the build
# but recoverable until a build finalizes the curation.
SKIPPED_DIRNAME = ".skipped"


def _purge_skipped(raw_dir: Path) -> None:
    """Permanently remove the skipped-source holding folder.

    Skip moves a source into ``raw/.skipped/`` so it can be un-skipped; a
    completed build finalizes those decisions, so the folder is cleared to
    reclaim disk. The skip verdicts stay in the manifest, so a later
    re-import of the same export is still recognized as skipped.

    Parameters
    ----------
    raw_dir: Path
        The raw output directory containing the holding folder.
    """
    skipped_dir = raw_dir / SKIPPED_DIRNAME
    if not skipped_dir.exists():
        return
    try:
        shutil.rmtree(skipped_dir)
    except OSError as e:
        logger.warning("Could not purge skipped folder: %s", e)


def _split_oversized(clusters: list[list[str]], max_size: int) -> list[list[str]]:
    """Split clusters larger than ``max_size`` into bounded batches.

    A single agent run has a bounded token budget, so any cluster larger
    than the cap is divided into batches that each fit within one run.

    Parameters
    ----------
    clusters: list[list[str]]
        Source clusters to bound.
    max_size: int
        Maximum number of sources per agent run.

    Returns
    -------
    list[list[str]]
        Work units, each with at most ``max_size`` sources.
    """
    units: list[list[str]] = []
    for cluster in clusters:
        if len(cluster) <= max_size:
            units.append(cluster)
            continue
        for start in range(0, len(cluster), max_size):
            units.append(cluster[start : start + max_size])
    return units


def _build_work_units(config: Config, raw_dir: Path, new_sources: list[str]) -> list[list[str]]:
    """Group staged sources into the per-run work units for this build.

    A reviewed cluster preview that still covers exactly the staged set is
    honored as-is, so the build matches what the user saw and tuned —
    regardless of the ``clustering.enabled`` flag, since producing a preview
    is itself the intent to cluster. With no matching preview, sources are
    clustered fresh only when clustering is enabled (the unattended path);
    otherwise each source compiles on its own. Oversized groups are split to
    stay within the per-run token budget.

    Parameters
    ----------
    config: Config
        Application configuration (clustering and embedding settings).
    raw_dir: Path
        Directory containing raw parsed source files.
    new_sources: list[str]
        Staged source paths relative to ``raw_dir``.

    Returns
    -------
    list[list[str]]
        Source groups, one per agent run.
    """
    from second_brain.clustering import cluster_scoped_sources, get_clusterer
    from second_brain.clustering.preview import (
        load_preview,
        preview_members,
        work_units_from_preview,
    )

    preview = load_preview(config.data_dir)
    if preview is not None and preview_members(preview) == set(new_sources):
        clusters = work_units_from_preview(config.data_dir) or [[s] for s in new_sources]
        logger.info("Using reviewed cluster preview (%d groups)", len(clusters))
    elif config.clustering.enabled:
        clusters = cluster_scoped_sources(
            new_sources,
            raw_dir,
            config.search,
            get_clusterer(config.clustering),
            config.clustering.sources,
            signature_chars=config.clustering.signature_chars,
        )
        logger.info("Auto-clustered %d sources into %d groups", len(new_sources), len(clusters))
    else:
        clusters = [[source] for source in new_sources]

    return _split_oversized(clusters, config.clustering.max_sources_per_run)


class _WikiToolExecutor:
    """
    Sandboxed tool execution scoped to the wiki directory.

    All file paths are resolved and validated to prevent the LLM agent
    from reading or writing outside the wiki and raw directories.
    """

    def __init__(
        self,
        wiki_dir: Path,
        raw_dir: Path,
        dry_run: bool = False,
        data_dir: Path | None = None,
    ) -> None:
        """
        Parameters:
        -----------
        wiki_dir: Path
            Root directory of the wiki.
        raw_dir: Path
            Directory containing raw parsed source files.
        dry_run: bool
            If ``True``, record intended writes without
            touching the filesystem.
        data_dir: Path | None
            Vault root; when set, each page change is appended to the build
            log as it happens so progress is observable mid-run.
        """
        self._wiki_dir = wiki_dir
        self._raw_dir = raw_dir
        self._dry_run = dry_run
        self._data_dir = data_dir
        self._changes: list[dict] = []

    def _record(self, action: str, rel_path: str) -> None:
        """Record a page change and append it to the build log immediately."""
        self._changes.append({"action": action, "path": rel_path})
        if self._data_dir is not None and not self._dry_run:
            from second_brain.build_log import append_build_actions

            append_build_actions(self._data_dir, [{"action": action, "path": rel_path}])

    def _resolve(self, rel_path: str) -> Path:
        """
        Resolve a relative path to the wiki or raw directory.

        Paths starting with ``raw/`` or ``../raw/`` are rooted to
        the raw directory; all others resolve against the wiki
        directory. Both are checked for directory-traversal attacks.

        Parameters:
        -----------
        rel_path: str
            Relative path as provided by the LLM agent.

        Returns:
        --------
        Path
            Resolved absolute path.

        Raises:
        -------
        PermissionError
            If the resolved path escapes its sandbox.
        """
        if rel_path.startswith("raw/") or rel_path.startswith("../raw/"):
            clean = rel_path.replace("../raw/", "").replace("raw/", "")
            resolved = (self._raw_dir / clean).resolve()
            if not str(resolved).startswith(str(self._raw_dir.resolve())):
                raise PermissionError(f"Path escapes raw directory: {rel_path}")
            return resolved

        resolved = (self._wiki_dir / rel_path).resolve()
        if not str(resolved).startswith(str(self._wiki_dir.resolve())):
            raise PermissionError(f"Path escapes wiki directory: {rel_path}")
        return resolved

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
                return self._read(tool_input["path"])
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

    def _read(self, rel_path: str) -> str:
        path = self._resolve(rel_path)
        if not path.exists():
            return f"File not found: {rel_path}"
        text = path.read_text(encoding="utf-8")
        if len(text) > _MAX_READ_CHARS:
            return (
                text[:_MAX_READ_CHARS]
                + f"\n\n[truncated: {_MAX_READ_CHARS} of {len(text)} chars shown — "
                "use grep_files to locate specific sections]"
            )
        return text

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
        # Distinguish a new page from a rewrite for the build log.
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

    def _glob(self, pattern: str) -> str:
        """
        Find files matching a glob pattern.

        Search is restricted to known content directories to
        prevent the agent from listing arbitrary paths.

        Parameters
        ----------
        pattern: str
            Glob pattern (e.g., ``"concepts/*.md"``).

        Returns
        -------
        str
            Newline-separated relative paths, or
            ``"No matches found"``.
        """
        matches = []
        for content_dir in ("concepts", "problems", "projects", "insights", "_meta"):
            dir_path = self._wiki_dir / content_dir
            if not dir_path.exists():
                continue
            for f in dir_path.rglob("*"):
                if f.is_file() and fnmatch.fnmatch(f.name, pattern.split("/")[-1]):
                    matches.append(str(f.relative_to(self._wiki_dir)))
        return "\n".join(sorted(matches)) if matches else "No matches found"

    def _grep(self, pattern: str, file_glob: str) -> str:
        """
        Search file contents for a regex pattern.

        Results are capped at 100 matches to prevent excessively
        large tool results from consuming context window.

        Parameters
        ----------
        pattern: str
            Regex pattern (matched case-insensitively).
        file_glob: str
            Glob restricting which files to search.

        Returns
        -------
        str
            Newline-separated ``path:line: content`` matches,
            or ``"No matches found"``.
        """
        compiled = re.compile(pattern, re.IGNORECASE)
        results: list[str] = []
        for md_file in self._wiki_dir.rglob(file_glob.lstrip("*").lstrip("/")):
            if not md_file.is_file():
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    rel = md_file.relative_to(self._wiki_dir)
                    results.append(f"{rel}:{i}: {line.strip()}")
            if len(results) > 100:
                results.append("... (truncated)")
                break
        return "\n".join(results) if results else "No matches found"

    @property
    def changes(self) -> list[dict]:
        return self._changes


def _find_new_sources(config: Config, manifest: Manifest) -> list[str]:
    """
    Find raw source files that haven't been compiled yet.

    Parameters
    ----------
    config: Config
        Application configuration.
    manifest: Manifest
        Ingestion manifest tracking compiled paths.

    Returns
    -------
    list[str]
        Sorted relative paths of uncompiled raw sources.
    """
    raw_dir = config.raw_dir
    if not raw_dir.exists():
        return []

    compiled = manifest.get_compiled_raw_paths()
    new_sources: list[str] = []

    for md_file in raw_dir.rglob("*.md"):
        relative = md_file.relative_to(raw_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue  # skip the .skipped/ holding folder and other dotfiles
        rel = str(relative)
        if rel not in compiled:
            new_sources.append(rel)

    return sorted(new_sources)


def run_compilation(
    config: Config,
    manifest: Manifest,
    force_full: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Run the full compilation pipeline.

    Executes agent-driven content synthesis, deterministic
    structure rebuild, and git auto-commit.

    Parameters
    ----------
    config: Config
        Application configuration.
    manifest: Manifest
        Ingestion manifest tracking compiled paths.
    force_full: bool
        If ``True``, recompile all sources regardless of
        manifest state.
    dry_run: bool
        If ``True``, skip agent execution and git commit.

    Returns
    -------
    dict
        Summary statistics including ``sources_compiled`` and
        all keys from ``rebuild_structure``.
    """
    wiki_dir = config.wiki_dir
    raw_dir = config.raw_dir

    from second_brain.compilation.schema import load_schema, write_default_schema

    if not (wiki_dir / "_meta" / "topic_schema.yaml").exists():
        write_default_schema(wiki_dir)

    load_schema(wiki_dir)

    if force_full:
        new_sources = sorted(str(f.relative_to(raw_dir)) for f in raw_dir.rglob("*.md"))
    else:
        new_sources = _find_new_sources(config, manifest)

    if not new_sources:
        logger.info("No new sources to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0}

    # Triage already ran during ingestion (free, local). Here we just
    # filter to the worthwhile set from the recorded decisions; any
    # untriaged file (e.g. triage was disabled) passes through.
    if config.triage.enabled:
        from second_brain.triage.pipeline import triage_pending, worthwhile_sources

        # Catch anything ingested before triage existed.
        triage_pending(config, manifest)
        new_sources = worthwhile_sources(manifest, new_sources)

    if not new_sources:
        logger.info("Nothing worthwhile to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0}

    logger.info("Compiling %d worthwhile sources", len(new_sources))

    compiled_count = 0
    if not dry_run:
        # Fail before any heartbeat or git work so a keyless build gives one
        # clear message instead of a generic per-group failure in the log.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Add your Anthropic API key in the "
                "app's Settings, or to a .env file at the repository root, then "
                "build again."
            )

        from second_brain.status import (
            clear_status,
            clear_stop,
            now_iso,
            stop_requested,
            touch_status,
            write_status,
        )

        # Group related sources into one run so a topic compiles once
        # rather than once per near-duplicate source.
        clear_stop(config.data_dir)  # drop any stale flag from a prior run
        started = now_iso()

        work_units = _build_work_units(config, raw_dir, new_sources)

        total = len(work_units)
        cumulative_cost = 0.0

        # A single agent turn can block longer than the heartbeat staleness
        # window; refresh it on a timer so progress stays visible.
        stop_heartbeat = threading.Event()

        def _keepalive() -> None:
            while not stop_heartbeat.wait(5.0):
                touch_status(config.data_dir)

        heartbeat = threading.Thread(target=_keepalive, daemon=True)
        heartbeat.start()
        cost_cap = config.compilation.max_cost_per_build_usd
        was_stopped = False
        try:
            for index, unit in enumerate(work_units):
                if stop_requested(config.data_dir):
                    logger.info("Stop requested — halting before group %d/%d", index + 1, total)
                    was_stopped = True
                    break
                if cost_cap > 0 and cumulative_cost >= cost_cap:
                    logger.info(
                        "Cost cap reached (~$%.2f >= $%.2f) — stopping before group %d/%d; "
                        "%d left staged",
                        cumulative_cost, cost_cap, index + 1, total, total - index,
                    )
                    break
                write_status(
                    config.data_dir, phase="compile", current=index,
                    total=total, started_at=started, cost_usd=cumulative_cost,
                )
                try:
                    cost = _run_agent(
                        config, wiki_dir, raw_dir, unit,
                        started_at=started, base_cost=cumulative_cost,
                        progress=(index, total),
                    )
                except Exception:
                    # A transient failure (rate limit, network) on one group
                    # shouldn't abort the whole batch. Leave it uncompiled so
                    # the next build retries it.
                    logger.exception("Compile failed for %s — skipping", ", ".join(unit))
                    continue
                if stop_requested(config.data_dir):
                    # Stopped mid-group: discard its partial, uncommitted
                    # pages and leave it uncompiled so the next build redoes
                    # it cleanly. Earlier groups are already committed.
                    _git_restore(wiki_dir)
                    logger.info("Stopped during %s — rolled back partial work", ", ".join(unit))
                    was_stopped = True
                    break
                cumulative_cost += cost
                manifest.mark_compiled(unit)
                _git_commit(wiki_dir)  # commit each completed group
                compiled_count += len(unit)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2.0)
            clear_stop(config.data_dir)
            clear_status(config.data_dir)
        logger.info("Compiled %d sources for ~$%.2f", compiled_count, cumulative_cost)

        # A finished build finalizes the user's curation, so reclaim the
        # skipped-source holding folder and consume the cluster preview. A
        # user-stopped build leaves them, since the decisions aren't final.
        if not was_stopped:
            _purge_skipped(raw_dir)
            from second_brain.clustering.preview import clear_preview

            clear_preview(config.data_dir)
    else:
        logger.info("[dry-run] Would compile: %s", new_sources)

    stats = rebuild_structure(wiki_dir)

    if not dry_run:
        _git_commit(wiki_dir)

    return {**stats, "sources_compiled": compiled_count}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough USD estimate from token counts (Sonnet pricing).

    Parameters
    ----------
    model: str
        Model name (currently all priced as Sonnet).
    input_tokens: int
        Cumulative input tokens this run.
    output_tokens: int
        Cumulative output tokens this run.

    Returns
    -------
    float
        Estimated cost in USD.
    """
    # approximate pricing in USD for live API cost readout
    price_per_mtok = {
        "claude-sonnet-4-6": {
            "input": 3.0, "output": 15.0
        }
        # TODO: if supporting, add more models
    }
    return (
        input_tokens / 1_000_000 * price_per_mtok[model]["input"]
        + output_tokens / 1_000_000 * price_per_mtok[model]["output"]
    )


def _run_agent(
    config: Config,
    wiki_dir: Path,
    raw_dir: Path,
    sources: list[str],
    started_at: str,
    base_cost: float = 0.0,
    progress: tuple[int, int] | None = None,
) -> float:
    """
    Invoke the compilation agent via the Anthropic API.

    Runs a multi-turn tool-use loop (up to ``max_iterations``) where the
    agent reads sources and writes/edits wiki pages, updating the live
    status heartbeat (elapsed + cumulative cost) as it goes.

    Parameters
    ----------
    config: Config
        Application configuration (provides model name).
    wiki_dir: Path
        Root directory of the wiki.
    raw_dir: Path
        Directory containing raw parsed source files.
    sources: list[str]
        Relative paths of source documents to compile (typically one).
    started_at: str
        ISO timestamp of the overall Build run (for elapsed display).
    base_cost: float
        Cost already spent by earlier files in this Build, so the
        heartbeat shows a cumulative figure.
    progress: tuple[int, int] | None
        ``(index, total)`` of this file within the Build, for the i/n
        readout.

    Returns
    -------
    float
        The USD cost of this single agent run.
    """
    from second_brain.status import stop_requested, write_status

    client = anthropic.Anthropic()
    executor = _WikiToolExecutor(wiki_dir, raw_dir, data_dir=config.data_dir)

    prompt = build_compilation_prompt(sources, wiki_dir)

    messages: list[dict] = [{"role": "user", "content": prompt}]

    # Cache the static prefix (system prompt + tool schemas) so it isn't
    # re-billed at full input rate on every turn.
    system = [{
        "type": "text",
        "text": COMPILATION_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    tools = [dict(t) for t in WIKI_TOOLS]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

    max_iterations = config.compilation.max_iterations
    token_budget = config.compilation.token_budget_per_run
    total_input_tokens = 0
    total_output_tokens = 0
    cur, tot = progress if progress else (0, 0)

    for iteration in range(max_iterations):
        # Honor a cancel between turns (the costly call is below), so a
        # stop lands within one agent round-trip.
        if stop_requested(config.data_dir):
            logger.info("Stop requested — halting agent after %d iterations", iteration)
            break

        # Shrink stale tool outputs before re-sending the history, so a
        # large early file read isn't billed on every later turn.
        _compact_history(messages)

        response = client.messages.create(
            model=config.compilation.model,
            max_tokens=8192,
            system=system,
            tools=tools,
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        total_tokens = total_input_tokens + total_output_tokens
        cost = _estimate_cost(config.compilation.model, total_input_tokens, total_output_tokens)
        write_status(
            config.data_dir, phase="compile", current=cur, total=tot,
            started_at=started_at, cost_usd=base_cost + cost,
        )
        logger.debug(
            "Iteration %d: +%d in (+%d cached), +%d out (cumulative %d / budget %d)",
            iteration + 1,
            response.usage.input_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            response.usage.output_tokens,
            total_tokens,
            token_budget,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            logger.info("Agent completed after %d iterations", iteration + 1)
            break

        # Hard stop before the next (most expensive) turn if we've blown the
        # budget. Checked after appending the assistant turn so the partial
        # work done so far is preserved on disk.
        if total_tokens >= token_budget:
            logger.warning(
                "Token budget exceeded (%d >= %d) after %d iterations — stopping early",
                total_tokens,
                token_budget,
                iteration + 1,
            )
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = executor.execute(block.name, block.input)
                logger.debug("Tool %s(%s) -> %s", block.name, block.input, result[:200])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

        if not tool_results:
            break

        messages.append({"role": "user", "content": tool_results})

    cost = _estimate_cost(config.compilation.model, total_input_tokens, total_output_tokens)
    logger.info(
        "Agent finished: %d changes, %d in + %d out tokens, ~$%.2f",
        len(executor.changes),
        total_input_tokens,
        total_output_tokens,
        cost,
    )
    return cost


def _compact_history(messages: list[dict], keep_last: int = 2, max_chars: int = 600) -> None:
    """Shrink large tool outputs in older turns to keep requests small.

    The agent re-sends the whole message history each turn, so an early
    full-file read would otherwise inflate every later request (and blow
    low input-token-per-minute rate limits). The most recent ``keep_last``
    user turns are left intact; older oversized tool results are replaced
    with a placeholder. The agent can re-read a file if it still needs it.

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
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    protected = set(user_indices[-keep_last:])
    for i, message in enumerate(messages):
        if message.get("role") != "user" or i in protected:
            continue
        content = message.get("content")
        if not isinstance(content, list):  # the initial prompt is a plain string
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("content"), str)
                and len(block["content"]) > max_chars
            ):
                block["content"] = placeholder


def _git_restore(wiki_dir: Path) -> None:
    """
    Discard uncommitted wiki changes, restoring to the last commit.

    Used when a build is stopped mid-source so the partial pages it wrote
    don't linger. Best-effort: failures are logged, not raised.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    """
    if not (wiki_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "reset", "--hard"], cwd=wiki_dir, capture_output=True, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=wiki_dir, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("Could not roll back wiki to last commit: %s", e)


def _git_commit(wiki_dir: Path) -> None:
    """
    Auto-commit wiki changes if the directory is a git repo.

    Initializes a repository if none exists, then stages and
    commits all changes.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    """
    git_dir = wiki_dir / ".git"
    if not git_dir.exists():
        try:
            subprocess.run(
                ["git", "init"],
                cwd=wiki_dir,
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Could not initialize git in wiki directory")
            return

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=wiki_dir,
            capture_output=True,
            check=True,
        )
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            logger.info("No wiki changes to commit")
            return

        subprocess.run(
            ["git", "commit", "-m", "auto: compilation + structure rebuild"],
            cwd=wiki_dir,
            capture_output=True,
            check=True,
        )
        logger.info("Committed wiki changes")
    except subprocess.CalledProcessError as e:
        logger.warning("Git commit failed: %s", e.stderr)
