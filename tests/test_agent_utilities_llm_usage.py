"""AgentUtilities.llm(): usage capture that agent_react's per-turn token
accounting (the context-meter badge) depends on.

llm() only ever returns response.choices[0].message to its caller — the
top-level `response` object (where `.usage` actually lives) is discarded.
agent_react._capture_llm_usage() falls back to `AGU.last_llm_usage` for
exactly this shape, so llm() must stash usage there before unwrapping.

Run with the app's venv (the one whose editable install points at this repo):

    system/venv/Scripts/python -m pytest dev/renglo-lib/tests
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from renglo.agent.agent_utilities import AgentUtilities


@pytest.fixture(autouse=True)
def _no_debug_json(monkeypatch):
    # llm() calls djson() twice per call; skip the disk writes in tests.
    monkeypatch.setenv("DEBUG_JSON", "false")


def _agu():
    """An AgentUtilities without __init__ (it wants live OpenAI/AWS config)."""
    agu = AgentUtilities.__new__(AgentUtilities)
    agu.last_llm_usage = None
    return agu


def _fake_response(content="hi", tool_calls=None, usage=None):
    message = SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_llm_stashes_usage_from_the_top_level_response():
    agu = _agu()
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=15, total_tokens=135)
    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.return_value = _fake_response(usage=usage)

    resp = agu.llm({"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}]})

    assert resp.content == "hi"  # still just the .message, unchanged behavior
    assert agu.last_llm_usage == {
        "prompt_tokens": 120,
        "completion_tokens": 15,
        "total_tokens": 135,
        "cached_tokens": 0,
    }


def test_llm_captures_cached_tokens_from_prompt_tokens_details():
    agu = _agu()
    details = SimpleNamespace(cached_tokens=40)
    usage = SimpleNamespace(
        prompt_tokens=120, completion_tokens=15, total_tokens=135, prompt_tokens_details=details,
    )
    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.return_value = _fake_response(usage=usage)

    agu.llm({"model": "gpt-4.1-mini", "messages": []})

    assert agu.last_llm_usage["cached_tokens"] == 40


def test_llm_clears_stale_usage_when_response_has_none():
    agu = _agu()
    agu.last_llm_usage = {"prompt_tokens": 999, "completion_tokens": 999,
                           "total_tokens": 999, "cached_tokens": 999}
    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.return_value = _fake_response(usage=None)

    agu.llm({"model": "gpt-4.1-mini", "messages": []})

    assert agu.last_llm_usage is None


def test_llm_clears_stale_usage_on_error_instead_of_returning_a_previous_calls_numbers():
    agu = _agu()
    agu.last_llm_usage = {"prompt_tokens": 999}
    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.side_effect = RuntimeError("boom")

    result = agu.llm({"model": "gpt-4.1-mini", "messages": []})

    assert result is False
    assert agu.last_llm_usage is None


def test_llm_usage_capture_never_raises_on_a_malformed_usage_object():
    agu = _agu()
    # .prompt_tokens_details raises instead of returning a value/None.
    class BombUsage:
        prompt_tokens = 10
        completion_tokens = 2
        total_tokens = 12

        @property
        def prompt_tokens_details(self):
            raise RuntimeError("boom")

    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.return_value = _fake_response(usage=BombUsage())

    resp = agu.llm({"model": "gpt-4.1-mini", "messages": []})

    assert resp is not False  # the actual LLM call still succeeded
    assert agu.last_llm_usage is None  # but usage capture backed off cleanly
