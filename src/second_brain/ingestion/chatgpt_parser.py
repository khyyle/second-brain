"""ChatGPT JSON export parser — converts conversations to individual markdown files."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from second_brain.parsing.output import write_parse_output
from second_brain.parsing.provider import ParseBlock, ParseLane, ParseResult

logger = logging.getLogger(__name__)


def _slugify(title: str) -> str:
    """Convert a conversation title to a filesystem-safe kebab-case slug.

    Truncated to 80 chars to avoid path length issues on macOS (1024
    limit can be hit with nested directories + conversation ID suffix).

    Parameters
    ----------
    title: str
        Raw conversation title.

    Returns
    -------
    str
        Lowercased, kebab-cased slug capped at 80 characters.
    """
    slug = title.lower().strip()
    slug = "".join(c if c.isalnum() or c in (" ", "-") else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:80] or "untitled"


def _format_timestamp(ts: float | int | None) -> str:
    """Convert a Unix timestamp to an ISO date string.

    Parameters
    ----------
    ts: float | int | None
        Unix epoch seconds, or ``None`` if unavailable.

    Returns
    -------
    str
        ISO date (``YYYY-MM-DD``) or ``"unknown"`` when *ts* is None.
    """
    if ts is None:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def _extract_message_text(message: dict) -> str | None:
    """Extract text content from a ChatGPT message node.

    Parameters
    ----------
    message: dict
        A single message object from the ChatGPT export mapping.

    Returns
    -------
    str | None
        Concatenated text parts, or ``None`` if the message has no
        extractable text.
    """
    content = message.get("content", {})
    parts = content.get("parts")
    if not parts:
        return None

    # ChatGPT export format uses "parts" that can be plain strings
    # or dicts with a "text" key (for multimodal messages)
    texts = []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict) and "text" in part:
            texts.append(part["text"])
    return "\n".join(texts) if texts else None


def _conversation_to_parse_result(conversation: dict) -> ParseResult | None:
    """Convert a single ChatGPT conversation dict to a ParseResult.

    Parameters
    ----------
    conversation: dict
        One conversation object from the ``conversations.json`` array.

    Returns
    -------
    ParseResult | None
        Structured result with markdown and blocks, or ``None`` if the
        entry is not a conversation object or has no user/assistant
        messages.
    """
    if not isinstance(conversation, dict):
        return None
    title = conversation.get("title", "Untitled Conversation")
    create_time = conversation.get("create_time")
    conversation_id = conversation.get("id", conversation.get("conversation_id", "unknown"))

    mapping = conversation.get("mapping", {})
    if not mapping:
        return None

    # The mapping dict is keyed by node id, not ordered chronologically, so
    # sort by create_time to reconstruct the real conversation flow.
    ordered_messages: list[tuple[str, str, float]] = []
    for node in mapping.values():
        message = node.get("message")
        if message is None:
            continue
        role = message.get("author", {}).get("role", "unknown")
        if role not in ("user", "assistant"):
            continue
        text = _extract_message_text(message)
        if not text or not text.strip():
            continue
        created_at = message.get("create_time", 0) or 0
        ordered_messages.append((role, text, created_at))

    ordered_messages.sort(key=lambda entry: entry[2])

    if not ordered_messages:
        return None

    md_lines = [
        "---",
        f'title: "{title}"',
        "type: chatgpt-conversation",
        f"conversation_id: {conversation_id}",
        f"date: {_format_timestamp(create_time)}",
        "---",
        "",
        f"# {title}",
        "",
    ]

    blocks: list[ParseBlock] = []
    for order, (role, text, _) in enumerate(ordered_messages):
        label = "**User**" if role == "user" else "**Assistant**"
        md_lines.append(f"### {label}\n")
        md_lines.append(text)
        md_lines.append("")

        blocks.append(
            ParseBlock(
                content=text,
                block_type="text",
                page_number=1,
                reading_order=order,
            )
        )

    return ParseResult(
        markdown="\n".join(md_lines),
        blocks=blocks,
        metadata={
            "source": f"chatgpt/{conversation_id}",
            "parse_lane": ParseLane.PASSTHROUGH.value,
            "conversation_id": conversation_id,
            "title": title,
            "date": _format_timestamp(create_time),
        },
    )


def _resolve_conversation_files(export_path: Path) -> list[Path]:
    """Locate conversation JSON files from a ChatGPT data export.

    Handles both the legacy single-file format (``conversations.json``)
    and the newer split format (``conversations-000.json``, etc.).

    Parameters
    ----------
    export_path: Path
        A ``.json`` file or the extracted export directory.

    Returns
    -------
    list[Path]
        Sorted list of conversation JSON file paths.

    Raises
    ------
    ValueError
        If *export_path* is not a ``.json`` file or directory.
    FileNotFoundError
        If no conversation files are found.
    """
    if export_path.suffix == ".json" and export_path.is_file():
        return [export_path]

    if not export_path.is_dir():
        raise ValueError(f"Expected .json file or directory, got: {export_path}")

    single = export_path / "conversations.json"
    if single.exists():
        return [single]

    split_files = sorted(export_path.glob("conversations-*.json"))
    if split_files:
        return split_files

    raise FileNotFoundError(f"No conversations.json or conversations-*.json in {export_path}")


def process_chatgpt_export(
    export_path: Path,
    output_dir: Path,
) -> list[Path]:
    """Parse a ChatGPT export and write each conversation as markdown.

    Accepts a single ``conversations.json``, an extracted export directory
    containing one, or a directory with the split format
    (``conversations-000.json``, ``conversations-001.json``, ...).

    Parameters
    ----------
    export_path: Path
        Path to the JSON file or extracted export directory.
    output_dir: Path
        Directory to write per-conversation markdown files into.

    Returns
    -------
    list[Path]
        Paths to the generated markdown files.

    Raises
    ------
    ValueError
        If *export_path* is not a ``.json`` file or directory, a file is
        not a JSON array, or no conversations could be parsed (i.e. it is
        not a ChatGPT export).
    FileNotFoundError
        If ``conversations.json`` cannot be located.
    """
    json_paths = _resolve_conversation_files(export_path)

    conversations: list[dict] = []
    for json_path in json_paths:
        with open(json_path, encoding="utf-8") as json_file:
            parsed = json.load(json_file)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected a JSON array in {json_path.name}")
        conversations.extend(parsed)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    skipped = 0

    for conversation in conversations:
        result = _conversation_to_parse_result(conversation)
        if result is None:
            skipped += 1
            continue

        slug = _slugify(conversation.get("title", "untitled"))
        conversation_id = conversation.get("id", conversation.get("conversation_id", ""))
        # Disambiguate conversations that share a title.
        stem = f"{slug}-{conversation_id[:8]}" if conversation_id else slug

        md_path, _ = write_parse_output(result, output_dir, stem)
        output_paths.append(md_path)

    # A file that parsed as JSON but yielded no conversations is not a usable
    # ChatGPT export. Fail loudly so it surfaces for the user rather than
    # vanishing as a silent no-op.
    if not output_paths:
        raise ValueError(
            f"No conversations found in {export_path.name} — "
            "expected a ChatGPT conversations export"
        )

    logger.info(
        "Parsed %d conversations (%d skipped) from %s",
        len(output_paths),
        skipped,
        export_path,
    )
    return output_paths
