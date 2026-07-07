"""Tests for the thin provider factory. Run: ``python test_client.py``."""

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
)

from renglo.agent.llm.client import resolve_provider, _build  # noqa: E402
from renglo.agent.llm.model_config import model_for  # noqa: E402

_GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_openai_models_use_default():
    for m in ("gpt-3.5-turbo", "gpt-4o-mini", "gpt-4o", "o3", "gpt-4.1"):
        assert resolve_provider(m) == (None, "OPENAI_API_KEY"), m


def test_gemini_and_gemma_route_to_google():
    assert resolve_provider("gemini-3.1-flash-lite") == (_GEMINI, "GEMINI_API_KEY")
    assert resolve_provider("gemma-3-27b") == (_GEMINI, "GEMINI_API_KEY")


def test_env_flip_routes_role_to_gemini():
    # Flipping a role's model (model_config) reroutes the provider end-to-end.
    key = "NOMA_MODEL_FAST"
    prev = os.environ.get(key)
    try:
        os.environ[key] = "gemini-3.1-flash-lite"
        base_url, key_env = resolve_provider(model_for("fast"))
        assert base_url == _GEMINI and key_env == "GEMINI_API_KEY"
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def test_build_requires_key():
    # A key_env that exists in neither env_config.py nor the environment must
    # raise a clear RuntimeError (not a cryptic SDK error).
    _build.cache_clear()
    raised = False
    try:
        _build(None, "DEFINITELY_MISSING_KEY_XYZ")
    except RuntimeError:
        raised = True
    finally:
        _build.cache_clear()
    assert raised, "expected RuntimeError when key missing"


if __name__ == "__main__":
    test_openai_models_use_default()
    test_gemini_and_gemma_route_to_google()
    test_env_flip_routes_role_to_gemini()
    test_build_requires_key()
    print("OK - all client factory tests passed")
