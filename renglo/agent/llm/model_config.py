"""Central registry for LLM model selection.

Single source of truth for which model each *role* uses. Handlers ask for a
role (e.g. ``model_for("baseline")``) instead of hardcoding a model string, so
switching models — or pointing a role at a different provider — is a one-line
change here (or an env var) instead of an edit across every handler.

Roles map to the distinctions the codebase already relied on informally:

    baseline  - cheap, high-volume chat / multi-step extraction
    fast       - the "smarter cheap" tier (structured output, light reasoning)
    dispatch   - agent tool-dispatch turns (fast decisions in a loop)
    reasoning  - deep, user-facing closing answers / hard reasoning
    planning   - itinerary / plan generation

Each role's default is the exact model in use before this registry existed, so
behavior is unchanged until a default is edited or an env var is set.

Override precedence (highest first):
    1. explicit ``override`` argument to ``model_for``
    2. ``NOMA_MODEL_<ROLE>`` environment variable
    3. the built-in default below
"""

import os

# Defaults preserve the exact models in use before centralization.
# _DEFAULTS = {
#     "baseline": "gpt-3.5-turbo",
#     "fast": "gpt-4o-mini",
#     "dispatch": "gpt-4o",
#     "reasoning": "o3",
#     "planning": "gpt-4.1",
# }

# _DEFAULTS = {
#     "baseline": 'gemini-3.1-flash-lite',
#     "fast": 'gemini-3.1-flash-lite',
#     "dispatch": 'gemini-3.1-flash-lite',
#     "reasoning": 'gemini-3.1-flash-lite',
#     "planning": 'gemini-3.1-flash-lite',
# }

_DEFAULTS = {
    "baseline":  "gemini-3.1-flash-lite",   # single-shot extraction — fine on 3.x
    "fast":      "gemini-3.1-flash-lite",
    "dispatch":  "gemini-2.5-flash",        # agent tool loop — needs a non-3.x model
    "reasoning": "gemini-2.5-flash",
    "planning": 'gemini-3.1-flash-lite',
}

def model_for(role: str, override: str | None = None) -> str:
    """Return the model id for a role.

    Args:
        role: one of the keys in ``_DEFAULTS``.
        override: if truthy, returned as-is (per-call override wins over all).

    Raises:
        KeyError: if ``role`` is unknown (fail loud — catches typos).
    """
    if override:
        return override
    if role not in _DEFAULTS:
        raise KeyError(
            f"Unknown model role {role!r}. Known roles: {sorted(_DEFAULTS)}"
        )
    return os.getenv(f"NOMA_MODEL_{role.upper()}") or _DEFAULTS[role]
