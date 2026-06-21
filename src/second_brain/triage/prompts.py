"""Triage prompt profiles.

Each profile is a complete instruction prompt (everything before the
document text, which the caller appends). They share an output schema
but differ in philosophy, decision criteria, and few-shot examples so
the user can tune what counts as "worthwhile" for their own brain via
``triage.profile`` in config.yaml.
"""

from __future__ import annotations

DEFAULT_PROFILE = "balanced"

_OUTPUT_SCHEMA = """\
Respond with ONE valid JSON object and nothing else (no prose, no code fence):
{
  "decision": "worthwhile" | "review" | "skip",
  "confidence": 0.0-1.0,
  "concept_hints": ["key", "concepts"],
  "content_type_hint": "concept" | "problem" | "project" | "insight",
  "domain_hints": ["domain1", "domain2"],
  "reason": "one concise sentence"
}"""


def _compose(role: str, philosophy: str, criteria: str, examples: list[str]) -> str:
    """Assemble a full profile prompt from its parts.

    Parameters
    ----------
    role: str
        One-line statement of the agent's job.
    philosophy: str
        The profile's guiding bias.
    criteria: str
        Bulleted worthwhile/review/skip rules.
    examples: list[str]
        Few-shot ``Document: ... -> {json}`` strings.

    Returns
    -------
    str
        The complete prompt text (document is appended by the caller).
    """
    example_block = "\n\n".join(examples)
    return (
        f"{role}\n\n{philosophy}\n\n{_OUTPUT_SCHEMA}\n\n"
        f"Decision criteria:\n{criteria}\n\n"
        f"Examples (study these boundaries carefully):\n{example_block}\n\n"
        "Now classify the document below. Judge by its actual content, not its title."
    )


def _ex(description: str, decision: str, ctype: str, reason: str) -> str:
    """Format a single few-shot example."""
    return (
        f"Document: {description}\n"
        f'-> {{"decision": "{decision}", "confidence": 0.9, '
        f'"content_type_hint": "{ctype}", "reason": "{reason}"}}'
    )


_BALANCED = _compose(
    role="You are a knowledge-triage agent for a personal wiki.",
    philosophy=(
        "Aim for a sensible middle ground: keep material with durable, "
        "reusable substance; drop ephemera and trivia; send genuinely "
        "ambiguous items to review for a human glance."
    ),
    criteria=(
        "- worthwhile: substantive explanations, worked solutions, designs, "
        "conclusions, or synthesis you would plausibly revisit\n"
        "- review: partially useful, fragmentary, or ambiguous value\n"
        "- skip: trivial lookups, logistics, small talk, duplicates, stubs"
    ),
    examples=[
        _ex(
            "Detailed design of a 6DoF foot-contact detection system with sensor "
            "choice and signal processing",
            "worthwhile",
            "project",
            "substantive technical design worth keeping",
        ),
        _ex(
            "Step-by-step solution computing critical points of f(x,y)=xy via partial derivatives",
            "worthwhile",
            "problem",
            "a complete worked example",
        ),
        _ex(
            "One-line question on removing None values from a Python list with a "
            "single snippet answer",
            "skip",
            "problem",
            "a trivial lookup with no durable value",
        ),
        _ex(
            "Drafting and wordsmithing a college admissions essay",
            "skip",
            "insight",
            "personal/logistics, not reusable knowledge",
        ),
        _ex(
            "Brainstorm of loosely-formed project ideas for an ESP32 wearable",
            "review",
            "project",
            "ideation that may or may not be worth promoting",
        ),
    ],
)


_TECHNICAL = _compose(
    role="You are a knowledge-triage agent for a STEM researcher's wiki.",
    philosophy=(
        "Technical depth is the entire point of this brain. Be GENEROUS with "
        "anything carrying real mathematical, scientific, or engineering "
        "substance: derivations, proofs, worked problems, algorithms, code "
        "design, system architecture, experiment notes, and debugging that "
        "reaches a resolution. Reserve skip for non-technical chatter and "
        "throwaway lookups. When a technical item is borderline, prefer "
        "worthwhile or review over skip."
    ),
    criteria=(
        "- worthwhile: any non-trivial technical reasoning, math/derivations, "
        "code or system design, worked problems, research notes, debugging "
        "with a resolution\n"
        "- review: technical but fragmentary, inconclusive, or possibly duplicate\n"
        "- skip: only non-technical logistics, personal/admin, small talk, or "
        "a single trivial one-line lookup"
    ),
    examples=[
        _ex(
            "Detailed design of a 6DoF foot-contact detection system: theory of "
            "operation, sensor selection, signal processing",
            "worthwhile",
            "project",
            "core engineering substance for a STEM brain",
        ),
        _ex(
            "Deriving the gradient of a scalar field with vector-calculus intuition",
            "worthwhile",
            "concept",
            "a reusable mathematical derivation",
        ),
        _ex(
            "Debugging ESP32 motor control and arriving at a working PWM config",
            "worthwhile",
            "project",
            "technical debugging with a concrete resolution",
        ),
        _ex(
            "One-line lookup: how to remove None from a Python list",
            "skip",
            "problem",
            "trivial single-snippet lookup",
        ),
        _ex(
            "Editing a college admissions essay about coursework",
            "skip",
            "insight",
            "non-technical personal writing",
        ),
    ],
)


_SKIP_HEAVY = _compose(
    role="You are a strict knowledge-triage agent guarding a high-signal wiki.",
    philosophy=(
        "The wiki must contain only distilled, durable knowledge. Most "
        "conversations are ephemeral and should NOT be promoted. Keep an item "
        "only if it produces a genuine, reusable conclusion or non-obvious "
        "synthesis you would deliberately return to. A problem you could simply "
        "redo, a routine explanation, or a brainstorm is not enough. Be "
        "aggressive: when in doubt, skip."
    ),
    criteria=(
        "- worthwhile: only a non-obvious conclusion, reusable result, or real "
        "cross-idea synthesis\n"
        "- review: rare borderline cases with clear potential\n"
        "- skip: lookups, routine Q&A, re-doable problems, brainstorms, anything "
        "you would not deliberately revisit"
    ),
    examples=[
        _ex(
            "Comparing Redis vs in-memory caching for a web app, ending in a "
            "clear recommendation with tradeoffs",
            "worthwhile",
            "insight",
            "a reusable conclusion with rationale",
        ),
        _ex(
            "Step-by-step critical-point calculation you could redo from scratch",
            "skip",
            "problem",
            "a re-doable exercise, not durable knowledge",
        ),
        _ex(
            "Verbose theory-of-operation write-up for a sensor system",
            "review",
            "project",
            "substantive but unfocused; only maybe worth it",
        ),
        _ex(
            "Brainstorm of ESP32 project ideas",
            "skip",
            "project",
            "ephemeral ideation",
        ),
        _ex(
            "How to remove None from a Python list",
            "skip",
            "problem",
            "trivial lookup",
        ),
    ],
)


_PROJECT_HEAVY = _compose(
    role="You are a knowledge-triage agent for a builder's project-focused wiki.",
    philosophy=(
        "Prioritize things being BUILT: systems, hardware, software, "
        "experiments, products, and the decisions behind them. Architecture, "
        "implementation choices, debugging of a real build, experiment results, "
        "and product/startup thinking are the highest value. Pure abstract "
        "theory or generic reference Q&A is lower priority unless tied to a "
        "concrete build."
    ),
    criteria=(
        "- worthwhile: project design, architecture, implementation decisions, "
        "experiment results, real-build debugging, product/startup reasoning\n"
        "- review: solid technical content not tied to a specific build, or "
        "pure theory that might support a project\n"
        "- skip: trivial lookups, small talk, generic Q&A unrelated to building"
    ),
    examples=[
        _ex(
            "Designing a 6DoF foot-contact detection build with sensor selection",
            "worthwhile",
            "project",
            "concrete system being built",
        ),
        _ex(
            "Debugging ESP32 motor control to a working configuration",
            "worthwhile",
            "project",
            "implementation debugging on a real build",
        ),
        _ex(
            "Brainstorm of ESP32 running-wearable project ideas",
            "worthwhile",
            "project",
            "project ideation worth capturing",
        ),
        _ex(
            "Abstract derivation of a gradient with no project context",
            "review",
            "concept",
            "useful theory but not tied to a build",
        ),
        _ex(
            "How to remove None from a Python list",
            "skip",
            "problem",
            "generic lookup unrelated to a project",
        ),
    ],
)


# --- lenient (super-lenient) ---------------------------------------------

_LENIENT = _compose(
    role="You are a permissive knowledge-triage agent that drops only pure noise.",
    philosophy=(
        "Keep almost everything that has ANY substance. Your only job is to "
        "filter out genuine noise: empty stubs, single trivial lookups, pure "
        "small talk, and exact duplicates. For everything else, lean "
        "worthwhile; when truly unsure, choose review rather than skip. It is "
        "much worse to drop something useful than to keep something marginal."
    ),
    criteria=(
        "- worthwhile: any conversation with more than a couple of substantive "
        "exchanges on any topic\n"
        "- review: thin but non-empty content\n"
        "- skip: only empty/stub conversations, a single trivial lookup, pure "
        "small talk, or exact duplicates"
    ),
    examples=[
        _ex(
            "Design discussion for a foot-contact detection system",
            "worthwhile",
            "project",
            "clearly substantive",
        ),
        _ex(
            "Step-by-step critical-point calculation",
            "worthwhile",
            "problem",
            "a worked example worth keeping",
        ),
        _ex(
            "Brainstorm of ESP32 project ideas",
            "worthwhile",
            "project",
            "substantive ideation",
        ),
        _ex(
            "Short exchange with one Python snippet for removing None",
            "review",
            "problem",
            "thin but has a reusable snippet",
        ),
        _ex(
            "Empty conversation with only a greeting",
            "skip",
            "insight",
            "no content",
        ),
    ],
)


TRIAGE_PROMPTS: dict[str, str] = {
    "balanced": _BALANCED,
    "technical": _TECHNICAL,
    "skip_heavy": _SKIP_HEAVY,
    "project_heavy": _PROJECT_HEAVY,
    "lenient": _LENIENT,
}

# Names only, for config validation without importing prompt content.
TRIAGE_PROFILE_NAMES: tuple[str, ...] = tuple(TRIAGE_PROMPTS.keys())


def get_prompt(profile: str) -> str:
    """Return the prompt for a profile, falling back to the default.

    Parameters
    ----------
    profile: str
        Profile name.

    Returns
    -------
    str
        The profile's prompt, or the default profile's prompt if unknown.
    """
    return TRIAGE_PROMPTS.get(profile, TRIAGE_PROMPTS[DEFAULT_PROFILE])
