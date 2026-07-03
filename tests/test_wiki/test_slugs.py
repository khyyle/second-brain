"""Tests for canonical slugification and wikilink-target resolution."""

from __future__ import annotations

from second_brain.wiki.slugs import (
    iter_wikilink_targets,
    normalize_link_list,
    normalize_link_target,
    normalize_wikilinks,
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


def test_slugify_collapses_hyphen_runs_and_trims_edges() -> None:
    # A dropped symbol (e.g. a minus sign in a math title) must not leave a
    # hyphen run, or the same name would slug two different ways.
    assert slugify("deep--learning") == "deep-learning"
    assert slugify("Critical Points of xy - x²y") == "critical-points-of-xy-x2y"
    assert slugify("-dot-product-") == "dot-product"


def test_slugify_maps_dash_family_and_underscores_to_hyphens() -> None:
    # An en dash or minus sign separates words just like a hyphen; dropping it
    # would glue the words into a different slug than the hyphen spelling.
    assert slugify("Borsuk–Ulam Theorem") == "borsuk-ulam-theorem"
    assert slugify("xy − x²y") == "xy-x2y"
    assert slugify("word_break") == "word-break"


def test_slugify_folds_unicode_to_ascii() -> None:
    # Accents decompose to base letters and superscripts to digits, so a stem
    # is always typeable and immune to lookalike-character mismatches.
    assert slugify("Ampère's Law") == "amperes-law"
    assert slugify("L'Hôpital's Rule") == "lhopitals-rule"


def test_slugify_spells_out_greek_letters() -> None:
    # Greek letters carry meaning in math; dropping them would collapse
    # distinct terms onto a shared stem (ρ-risk and α-risk both to "risk",
    # σ-algebra to the "algebra" domain) instead of keeping them apart.
    assert slugify("ρ-Risk") == "rho-risk"
    assert slugify("α-risk") == "alpha-risk"
    assert slugify("σ-algebra") == "sigma-algebra"
    assert slugify("β-VAE") == "beta-vae"


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


def test_iter_wikilink_targets_ignores_code_and_math() -> None:
    # A [[...]] inside a fenced code block or math is a literal bracket, not a
    # link, so it must not become a graph edge or a phantom gap.
    content = (
        "See [[Bayes Rays]].\n"
        "```python\n"
        "tokens = torch.tensor([[12, 305, 87, 999]])\n"
        "```\n"
        "Inline $[[2.5, 2.5]]$ and `df[['CHANNEL']]` too.\n"
    )
    assert iter_wikilink_targets(content) == ["bayes-rays"]


def test_normalize_link_list_canonicalizes_mixed_forms_to_wrapped_links() -> None:
    values = ["[[Neural Fields]]", "laplace-approximation", "Fisher Information"]
    assert normalize_link_list(values) == [
        "[[neural-fields]]",
        "[[laplace-approximation]]",
        "[[fisher-information]]",
    ]


def test_normalize_link_list_dedupes_preserving_order() -> None:
    values = ["Bayes Rays", "neural-fields", "[[bayes-rays]]"]
    assert normalize_link_list(values) == ["[[bayes-rays]]", "[[neural-fields]]"]


def test_normalize_link_list_skips_empty_and_non_strings() -> None:
    assert normalize_link_list(["!!!", "", 5, "Bayes Rays"]) == ["[[bayes-rays]]"]


def test_normalize_wikilinks_injects_display_when_target_changes() -> None:
    assert normalize_wikilinks("See [[Bayes Rays]].") == "See [[bayes-rays|Bayes Rays]]."


def test_normalize_wikilinks_leaves_bare_slug_alone() -> None:
    assert normalize_wikilinks("See [[bayes-rays]].") == "See [[bayes-rays]]."


def test_normalize_wikilinks_preserves_existing_display() -> None:
    text = "the [[trilinear-interpolation|trilinearly interpolated]] grid"
    assert normalize_wikilinks(text) == text


def test_normalize_wikilinks_slugs_target_keeps_display() -> None:
    assert normalize_wikilinks("[[Bayes Rays|the method]]") == "[[bayes-rays|the method]]"


def test_normalize_wikilinks_is_idempotent() -> None:
    once = normalize_wikilinks("See [[Bayes Rays]] and [[Neural Fields|fields]].")
    assert normalize_wikilinks(once) == once


def test_normalize_wikilinks_strips_path_and_suffix() -> None:
    assert normalize_wikilinks("[[concepts/foo.md|Foo]]") == "[[foo|Foo]]"


def test_normalize_wikilinks_ignores_links_in_fenced_code() -> None:
    text = "```python\nx = np.array([[1, 2], [3, 4]])\n```"
    assert normalize_wikilinks(text) == text


def test_normalize_wikilinks_ignores_links_in_inline_code() -> None:
    text = "Select with `df[['CHANNEL', 'FIXED_COST']]` here."
    assert normalize_wikilinks(text) == text


def test_normalize_wikilinks_ignores_links_in_inline_math() -> None:
    text = "The value $[[1, 2], [3, 4]]$ is a matrix."
    assert normalize_wikilinks(text) == text


def test_normalize_wikilinks_ignores_links_in_bracket_display_math() -> None:
    text = "\\[\nM = [[a, b], [c, d]]\n\\]"
    assert normalize_wikilinks(text) == text


def test_normalize_wikilinks_rewrites_prose_but_not_adjacent_code() -> None:
    text = "Use [[Bayes Rays]] like `arr[[0]]` does."
    expected = "Use [[bayes-rays|Bayes Rays]] like `arr[[0]]` does."
    assert normalize_wikilinks(text) == expected
