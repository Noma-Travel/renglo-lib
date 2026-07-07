"""Thin provider factory — pick the right client for a model.

Companion to ``model_config`` (which answers *which model name*). This answers
*which endpoint + key* that model needs, and hands back a ready OpenAI-SDK
client pointed at the right provider.

It relies on providers exposing an **OpenAI-compatible** chat-completions
endpoint, so the existing ``client.chat.completions.create(...)`` call sites and
``response.choices[0].message`` parsing keep working unchanged. That covers
OpenAI itself plus Gemini, Gemma, Groq, Together, Mistral, DeepSeek, Fireworks…

Provider is inferred from the model-name prefix:
    gemini-* / gemma-*  -> Google Generative Language (OpenAI-compatible) endpoint
    everything else       -> OpenAI default (gpt-*, o3, o1-, gpt-4.1, ...)

Keys come from ``renglo.common.load_config()`` — i.e. ``system/env_config.py``
locally and environment variables on Lambda (with an ``os.environ`` fallback):
    OpenAI  -> OPENAI_API_KEY
    Gemini  -> GEMINI_API_KEY

Flip a role to Gemini with e.g. ``NOMA_MODEL_FAST=gemini-3.1-flash-lite`` (see
``model_config``); routing then happens automatically at the call site.

NOTE: this thin layer does NOT cover providers without an OpenAI-compatible
endpoint (native Gemini SDK, Anthropic, Bedrock) or the OpenAI Responses API
(``.responses.*``). For those, see ADAPTER_INTERFACE.md.
"""

import os
from functools import lru_cache

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# (model-name prefix, base_url or None for OpenAI default, api-key env var)
_PROVIDERS = (
    ("gemini-", _GEMINI_BASE_URL, "GEMINI_API_KEY"),
    ("gemma-", _GEMINI_BASE_URL, "GEMINI_API_KEY"),
)
_DEFAULT = (None, "OPENAI_API_KEY")  # OpenAI: gpt-*, o3, o1-, gpt-4.1, ...


def resolve_provider(model: str) -> tuple[str | None, str]:
    """Return ``(base_url, api_key_env)`` for a model name."""
    for prefix, base_url, key_env in _PROVIDERS:
        if model.startswith(prefix):
            return base_url, key_env
    return _DEFAULT


@lru_cache(maxsize=1)
def _config():
    # Same key source the handlers use: env_config.py locally, env vars on Lambda.
    from renglo.common import load_config

    return load_config()


def _api_key(key_env: str) -> str | None:
    return _config().get(key_env) or os.getenv(key_env)


@lru_cache(maxsize=None)
def _build(base_url: str | None, key_env: str):
    # Resolve the key first so a missing key raises a clear error regardless of
    # whether the SDK is importable.
    api_key = _api_key(key_env)
    if not api_key:
        raise RuntimeError(
            f"Missing {key_env} for provider "
            f"(base_url={base_url or 'openai-default'}); "
            f"set it in system/env_config.py or the environment"
        )
    # Lazy import so resolve_provider / this module load without the SDK present
    # (and so tracing stays wired: langfuse.openai is a drop-in OpenAI client).
    from langfuse.openai import OpenAI

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def get_client(model: str):
    """Return an OpenAI-SDK client pointed at ``model``'s provider (cached)."""
    base_url, key_env = resolve_provider(model)
    return _build(base_url, key_env)


def complete(**prompt):
    """Drop-in for ``client.chat.completions.create(**prompt)`` that also selects
    the provider from ``prompt['model']``. Returns the raw response, unchanged."""
    return get_client(prompt["model"]).chat.completions.create(**prompt)
