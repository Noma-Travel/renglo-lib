"""AGU.llm transport retries — app-owned (SDK max_retries=0)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest

from renglo.agent.agent_utilities import AgentUtilities, _is_transient_llm_error


@pytest.fixture(autouse=True)
def _no_debug_json(monkeypatch):
    monkeypatch.setenv("DEBUG_JSON", "false")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("renglo.agent.agent_utilities.time.sleep", lambda *_a, **_k: None)


def _agu():
    agu = AgentUtilities.__new__(AgentUtilities)
    agu.last_llm_usage = None
    return agu


def _fake_response(content="hi", tool_calls=None, usage=None):
    message = SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=usage)


def _connection_error(msg="Connection error."):
    req = MagicMock()
    return openai.APIConnectionError(message=msg, request=req)


def test_is_transient_connection_and_timeout():
    assert _is_transient_llm_error(_connection_error()) is True
    assert _is_transient_llm_error(openai.APITimeoutError(request=MagicMock())) is True
    assert _is_transient_llm_error(RuntimeError("Connection error")) is True
    assert _is_transient_llm_error(RuntimeError("boom")) is False


def test_llm_retries_transient_then_succeeds():
    agu = _agu()
    agu.AI_2 = MagicMock()
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12)
    agu.AI_2.chat.completions.create.side_effect = [
        _connection_error(),
        _fake_response(content="recovered", usage=usage),
    ]

    resp = agu.llm({"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}]})

    assert resp.content == "recovered"
    assert agu.AI_2.chat.completions.create.call_count == 2
    assert agu.last_llm_usage["prompt_tokens"] == 10


def test_llm_does_not_retry_auth_error():
    agu = _agu()
    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.side_effect = openai.AuthenticationError(
        message="bad key",
        response=MagicMock(status_code=401, headers={}),
        body=None,
    )

    result = agu.llm({"model": "gpt-4.1-mini", "messages": []})

    assert result is False
    assert agu.AI_2.chat.completions.create.call_count == 1
    assert agu.last_llm_usage is None


def test_llm_returns_false_after_three_transient_failures():
    agu = _agu()
    agu.AI_2 = MagicMock()
    agu.AI_2.chat.completions.create.side_effect = [
        _connection_error("a"),
        _connection_error("b"),
        _connection_error("c"),
    ]

    result = agu.llm({"model": "gpt-4.1-mini", "messages": []})

    assert result is False
    assert agu.AI_2.chat.completions.create.call_count == 3
    assert agu.last_llm_usage is None
