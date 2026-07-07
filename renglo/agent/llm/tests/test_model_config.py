"""Tests for the model_config registry.

Runnable either with pytest or directly: ``python test_model_config.py``.
"""

import os
import sys

# Allow direct execution without installing the package.
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
)

from renglo.agent.llm.model_config import model_for, _DEFAULTS  # noqa: E402


def test_each_role_resolves_to_its_default():
    # Structural (not pinned to specific model names, which get tuned): with no
    # override/env, every role resolves to its configured default, non-empty.
    for role, default in _DEFAULTS.items():
        assert model_for(role) == default
        assert isinstance(default, str) and default


def test_explicit_override_wins():
    assert model_for("baseline", override="gpt-4o") == "gpt-4o"


def test_env_var_override(monkeypatch=None):
    key = "NOMA_MODEL_FAST"
    prev = os.environ.get(key)
    try:
        os.environ[key] = "gemini-3.1-flash-lite"
        assert model_for("fast") == "gemini-3.1-flash-lite"
        # explicit override still beats env
        assert model_for("fast", override="x") == "x"
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def test_unknown_role_raises():
    try:
        model_for("nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown role")


if __name__ == "__main__":
    test_each_role_resolves_to_its_default()
    test_explicit_override_wins()
    test_env_var_override()
    test_unknown_role_raises()
    print(f"OK - all model_config tests passed ({len(_DEFAULTS)} roles)")
