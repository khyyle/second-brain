from __future__ import annotations

import re

# Captures the target from [[target]] and [[target|display text]] wikilinks.
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def slugify(text: str) -> str:
    """
    Reduce arbitrary text to a kebab-case stem.

    Lower-cases, drops every character that is not alphanumeric, a space, or a
    hyphen, then collapses runs of whitespace into single hyphens. Unicode
    letters are kept (``str.isalnum`` is true for them), so an accented title
    keeps its letters while punctuation such as apostrophes, commas, and
    parentheses is dropped.

    Parameters
    ----------
    text: str
        Any human-readable string (a title, heading, or link label).

    Returns
    -------
    str
        The kebab-case stem, or an empty string when ``text`` has no slug-able
        characters. Already-slugged input is returned unchanged.
    """
    cleaned = "".join(char if char.isalnum() or char in " -" else "" for char in text.lower())
    return "-".join(cleaned.split())


def normalize_link_target(target: str) -> str:
    """
    Reduce any wikilink target, title, or page path to its bare stem slug.

    Accepts the many forms a reference takes:
    — wrapped ``[[target]]``
    - ``[[target|display]]``
    - folder-prefixed ``concepts/target``
    - ``.md`` suffix
    - ``#anchor``

    This function strips each down to the bare name, then applies
    `slugify` so the result matches the stem pages are keyed by. Already-bare
    input will pass through untouched.

    Parameters
    ----------
    target: str
        A raw link target, free-text reference, or page path.

    Returns
    -------
    str
        The resolved page stem, or an empty string when nothing slug-able
        remains.
    """
    text = target.strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    text = text.split("|", 1)[0]
    text = text.split("#", 1)[0]
    text = text.rsplit("/", 1)[-1]
    if text.endswith(".md"):
        text = text[:-3]
    return slugify(text)


def iter_wikilink_targets(text: str) -> list[str]:
    """
    Return every wikilink target in ``text``, each resolved to its stem slug.

    Parameters
    ----------
    text: str
        Markdown content that may contain ``[[wikilinks]]``.

    Returns
    -------
    list[str]
        Stems in document order, skipping any link that slugs to empty.
    """
    targets: list[str] = []
    for raw in _WIKILINK_RE.findall(text):
        stem = normalize_link_target(raw)
        if stem:
            targets.append(stem)
    return targets
