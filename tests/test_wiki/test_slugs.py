"""Tests for canonical slugification and wikilink-target resolution."""

from __future__ import annotations

from second_brain.wiki.slugs import (
    iter_wikilink_targets,
    normalize_link_target,
    slugify,
)


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("Bayes Rays") == "bayes-rays"
    assert slugify("Gradient   Descent") == "gradient-descent"


def test_slugify_drops_punctuation_to_match_filenames() -> None:
    # Apostrophes and other punctuation are dropped, exactly as page filenames
    # are derived, so a link to them resolves rather than becoming a gap.
    assert slugify("Bessel's Correction") == "bessels-correction"
    assert slugify("Why KFAC Don't Fix Marbling") == "why-kfac-dont-fix-marbling"


def test_slugify_is_idempotent_on_an_existing_slug() -> None:
    assert slugify("bayes-rays") == "bayes-rays"


def test_slugify_keeps_unicode_letters() -> None:
    assert slugify("Ampère's Law") == "ampères-law"


def test_slugify_empty_when_no_sluggable_characters() -> None:
    assert slugify("!!!") == ""


def test_normalize_link_target_strips_folder_suffix_and_anchor() -> None:
    assert normalize_link_target("concepts/exchange-traded-funds") == "exchange-traded-funds"
    assert normalize_link_target("point-estimation") == "point-estimation"
    assert normalize_link_target("concepts/foo.md") == "foo"
    assert normalize_link_target("concepts/foo#section") == "foo"
    assert normalize_link_target("  spaced  ") == "spaced"


def test_normalize_link_target_canonicalizes_title_case_and_wrappers() -> None:
    # The core fix: a Title-case body link now resolves to its kebab stem.
    assert normalize_link_target("Bayes Rays") == "bayes-rays"
    assert normalize_link_target("[[Bayes Rays]]") == "bayes-rays"
    assert normalize_link_target("[[bayes-rays|Bayes Rays]]") == "bayes-rays"
    assert normalize_link_target("Bayes' Theorem") == "bayes-theorem"


def test_iter_wikilink_targets_normalizes_both_styles() -> None:
    content = "See [[point-estimation]] and [[concepts/exchange-traded-funds|ETFs]]."
    assert iter_wikilink_targets(content) == ["point-estimation", "exchange-traded-funds"]


def test_iter_wikilink_targets_canonicalizes_title_case() -> None:
    content = "The [[Bayes Rays]] method extends [[Neural Fields]]."
    assert iter_wikilink_targets(content) == ["bayes-rays", "neural-fields"]
