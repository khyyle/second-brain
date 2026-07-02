from __future__ import annotations

import re
import unicodedata

# A wikilink, capturing its target and optional display label:
# [[target]] or [[target|display label]].
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# Spans whose [[...]] is literal text rather than a link like fenced code, inline
# code, and math. We mask these to avoid treating them as links. These are ordered widest
# first so a wide delimiter such as $$ is matched before a narrower $.
_PROTECTED_SPAN_RES = (
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"~~~.*?~~~", re.DOTALL),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"\$\$.*?\$\$", re.DOTALL),
    re.compile(r"\\\[.*?\\\]", re.DOTALL),
    re.compile(r"\\\(.*?\\\)", re.DOTALL),
    re.compile(r"\$[^$\n]+\$"),
)

# A masked span is replaced by its index wrapped in null bytes. Null bytes never
# occur in the markdown sources, so the placeholder cannot collide with real
# content.
_MASK_SENTINEL = "\x00{}\x00"
_MASK_RESTORE_RE = re.compile("\x00(\\d+)\x00")

# Characters that read as word separators but are not the ASCII hyphen: the
# Unicode hyphen/dash family, the minus sign, and the underscore. Mapped to "-"
# before ASCII folding so "Borsuk–Ulam" slugs to borsuk-ulam rather than the
# glued borsukulam a plain character drop would produce.
_SEPARATOR_TRANSLATION = str.maketrans(dict.fromkeys("‐‑‒–—―−_", "-"))


def slugify(text: str) -> str:
    """
    Reduce arbitrary text to an ASCII kebab-case stem.

    The text is lower-cased, dash-like characters and underscores become
    hyphens, and the rest is folded to ASCII: accented letters decompose to
    their base letters and superscripts to plain digits, while characters with
    no ASCII equivalent are dropped. Every remaining character that is not a
    letter, a digit, a space, or a hyphen is removed. Runs of whitespace and
    hyphens then collapse into single hyphens, and leading or trailing hyphens
    are trimmed, so a dropped symbol can never leave a hyphen run that would
    make the same name slug two different ways.

    Parameters
    ----------
    text: str
        Any human-readable string, such as a title, a heading, or a link label.

    Returns
    -------
    str
        The ASCII kebab-case stem. Text that already has this form is returned
        unchanged. Text with no usable characters returns an empty string.
    """
    text = text.lower().translate(_SEPARATOR_TRANSLATION)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(char if char.isalnum() or char in " -" else "" for char in text)
    return re.sub(r"-{2,}", "-", "-".join(cleaned.split())).strip("-")


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


def _mask_protected_spans(text: str) -> tuple[str, list[str]]:
    """
    Replace code and math spans with bracket-free placeholders.
    """
    spans: list[str] = []

    def _mask(match: re.Match) -> str:
        spans.append(match.group(0))
        return _MASK_SENTINEL.format(len(spans) - 1)

    masked = text
    for span_re in _PROTECTED_SPAN_RES:
        masked = span_re.sub(_mask, masked)
    return masked, spans


def _restore_protected_spans(text: str, spans: list[str]) -> str:
    """Put the masked code and math spans back where their placeholders sit."""
    return _MASK_RESTORE_RE.sub(lambda match: spans[int(match.group(1))], text)


def iter_wikilink_targets(text: str) -> list[str]:
    """
    Return the resolved stem of every wikilink in a block of markdown.

    Code and math spans are masked first, so a ``[[...]]`` inside them, such as
    an array or matrix literal, is not read as a link. Each remaining wikilink
    target is resolved with `normalize_link_target`. A link whose target has no
    usable characters is skipped.

    Parameters
    ----------
    text: str
        Markdown content that may contain wikilinks.

    Returns
    -------
    list[str]
        The resolved stems, in the order the links appear.
    """
    masked, _ = _mask_protected_spans(text)
    targets: list[str] = []
    for match in _WIKILINK_RE.finditer(masked):
        stem = normalize_link_target(match.group(1))
        if stem:
            targets.append(stem)
    return targets


def normalize_link_list(values: list[str]) -> list[str]:
    """
    Resolve a list of frontmatter edge values to canonical wikilinks.

    Frontmatter relationship fields such as prerequisites and related hold a
    list of references. Each reference is resolved to its stem with
    `normalize_link_target`, which accepts a wrapped wikilink, a bare stem, or
    free text naming a page that does not exist yet, then wrapped as a
    ``[[stem]]`` wikilink to keep the reference clickable in Obsidian
    and counted in the link graph. The first occurrence of each stem sets its
    position, and any later reference that resolves to a stem already seen is
    removed.

    Parameters
    ----------
    values: list[str]
        The raw entries from one frontmatter edge field.

    Returns
    -------
    list[str]
        The resolved references as ``[[stem]]`` wikilinks, deduplicated and in
        first-seen order. Entries that resolve to nothing, and entries that are
        not strings, are skipped.
    """
    canonical: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue
        slug = normalize_link_target(value)

        if slug and slug not in seen:
            seen.add(slug)
            canonical.append(f"[[{slug}]]")

    return canonical


def _rewrite_wikilink(match: re.Match) -> str:
    """Rewrite a single matched wikilink so its target is a canonical stem."""
    raw_target = match.group(1).strip()
    display = match.group(2)
    slug = normalize_link_target(raw_target)
    if not slug:
        # Target has no usable characters: leave the link as written.
        return match.group(0)
    if display is not None:
        # Label was given: resolve the target only and keep the label.
        return f"[[{slug}|{display}]]"
    if raw_target == slug:
        # Target is already a bare stem: no label needed.
        return f"[[{slug}]]"
    # Resolving changed the target: use the original text as the display label.
    return f"[[{slug}|{raw_target}]]"


def normalize_wikilinks(markdown: str) -> str:
    """
    Canonicalize every wikilink in a page body, leaving code and math untouched.

    Fenced code, inline code, and math spans are set aside before any rewriting,
    so bracketed expressions inside them, such as array or matrix literals, are
    never treated as links. Every remaining wikilink has its target resolved to
    a canonical stem while its display label is preserved. Running the function
    on a body that is already canonical returns the same body.

    Parameters
    ----------
    markdown: str
        A page body, without its frontmatter block.

    Returns
    -------
    str
        The body with every wikilink target canonicalized.
    """
    masked, spans = _mask_protected_spans(markdown)
    rewritten = _WIKILINK_RE.sub(_rewrite_wikilink, masked)
    return _restore_protected_spans(rewritten, spans)
