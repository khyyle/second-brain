"""Tests for the live description of what the build agent is doing."""

from __future__ import annotations

from second_brain.compilation.agent import describe_tool_call


def test_file_tools_name_the_page_or_source() -> None:
    assert describe_tool_call("read_file", {"path": "raw/chatgpt/kalman-filter.md"}) == (
        "Reading kalman-filter"
    )
    assert describe_tool_call("edit_file", {"path": "concepts/gradient-descent.md"}) == (
        "Editing gradient-descent"
    )


def test_writing_a_page_names_its_title() -> None:
    assert describe_tool_call("write_page", {"title": "Kalman Filter Update Step"}) == (
        "Writing Kalman Filter Update Step"
    )


def test_searches_quote_what_was_searched_for() -> None:
    assert describe_tool_call("search_wiki", {"query": "kalman filter"}) == (
        "Searching the wiki for 'kalman filter'"
    )


def test_an_unknown_tool_still_reads_as_progress() -> None:
    assert describe_tool_call("something_new", {}) == "Working"
