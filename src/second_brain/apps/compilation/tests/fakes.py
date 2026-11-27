"""Scripted model turns shared by agent and compiler behavior tests."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from second_brain.cloud_llm.pi import pi_helper

Turn = dict[str, Any]


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}


def tool_use(*calls: dict, cost: float | None = 0.01) -> Turn:
    """A turn in which the model asks for tools."""
    message = {"role": "assistant", "content": list(calls), "stopReason": "toolUse"}
    return {"outcome": "toolUse", "message": message, "cost_usd": cost}


def finished(cost: float | None = 0.01) -> Turn:
    """A turn in which the model says it is done."""
    message = {"role": "assistant", "content": [], "stopReason": "stop"}
    return {"outcome": "stop", "message": message, "cost_usd": cost}


def no_reply(outcome: str, cost: float | None = 0.0) -> Turn:
    """A turn the helper reports as unusable, such as ``error`` or ``too_large``."""
    return {"outcome": outcome, "cost_usd": cost}


class FakeTurns:
    """Answer each turn from a script. The last entry repeats.

    An entry is a helper result, a function of the Pi context that returns one, or an
    exception to raise. ``contexts`` holds a copy of what each turn was sent.
    """

    def __init__(self, *script: Turn | Callable[[dict], Turn] | Exception) -> None:
        self._script = script
        self.contexts: list[dict] = []

    def __call__(self, provider: str, model: str, context: dict, stop_requested: object) -> Turn:
        self.contexts.append(copy.deepcopy(context))
        entry = self._script[min(len(self.contexts), len(self._script)) - 1]
        if isinstance(entry, Exception):
            raise entry
        return copy.deepcopy(entry(context) if callable(entry) else entry)


def install_fake(
    monkeypatch: pytest.MonkeyPatch, *script: Turn | Callable[[dict], Turn] | Exception
) -> FakeTurns:
    turns = FakeTurns(*script)
    monkeypatch.setattr(pi_helper, "run_turn", turns)
    return turns
