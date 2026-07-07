# LLM Adapter Interface — implementation spec

> Hand this file back to Claude Code to implement. It is the follow-up to the
> **thin provider factory** (`client.py`) already in place. Read "Current state"
> first, then "What to build".

## Why this exists

The thin factory (`client.py`) routes calls to any provider that exposes an
**OpenAI-compatible** `chat.completions` endpoint (OpenAI, Gemini, Gemma, Groq,
Together, Mistral, …). It works by swapping the *client* at each call site while
handlers still speak the OpenAI SDK dialect directly:

```python
response = get_client(params['model']).chat.completions.create(**params)
resp = response.choices[0].message          # <-- OpenAI-shaped response
```

That is a **bet**: every provider returns `choices[0].message` and accepts
OpenAI's `tools`/`tool_choice` shape. The bet breaks for:

- **Native SDKs with no compatible endpoint** — native Gemini (`google-genai`),
  Anthropic (`anthropic`), AWS Bedrock. Different request format, different
  response object.
- **The OpenAI Responses API** (`.responses.parse` / `.responses.create`) — used
  in `add_flight_rextur.py:466`, `agent_utilities.py:842` (`llm_responses`), and
  `react_utilities.py:3033`. Gemini's compatible endpoint does not implement it,
  so these are pinned to OpenAI today.
- **Tool-calling divergence** — the part providers differ on most, and it is
  load-bearing for the agent + booking handlers.

The adapter interface removes the bet: handlers depend on ONE neutral method and
a normalized response, and per-provider adapters translate to/from each native
format.

## Current state (already done — do not redo)

- `renglo/agent/llm/model_config.py` — `model_for(role, override)`; roles
  `baseline`/`fast`/`dispatch`/`reasoning`/`planning`; `NOMA_MODEL_<ROLE>` env
  overrides. **Keep as the role→model-name source of truth.**
- `renglo/agent/llm/client.py` — `resolve_provider(model)`, `get_client(model)`
  (cached, `langfuse.openai` client), `complete(**prompt)`. Provider inferred
  from model-name prefix (`gemini-`/`gemma-` → Google endpoint; else OpenAI).
  Keys from env: `OPENAI_API_KEY`, `GEMINI_API_KEY`.
- All `chat.completions.create` call sites route through `get_client(...)`
  EXCEPT `agent_react.py` (deliberately excluded — it owns its `FAST_MODEL` /
  `REASONING_MODEL` constants) and the Responses-API sites listed above.
- Tests: `tests/test_model_config.py`, `tests/test_client.py`.

## What to build

### 1. Neutral request/response DTOs — `renglo/agent/llm/types.py`

```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str            # raw JSON string, as OpenAI returns it

@dataclass
class LLMMessage:
    role: str                 # "assistant"
    content: str | None
    tool_calls: list[ToolCall]

@dataclass
class LLMRequest:
    model: str
    messages: list[dict]      # neutral chat messages ({role, content, ...})
    temperature: float = 0.0
    tools: list[dict] | None = None          # neutral tool schema (OpenAI-style is the canonical form)
    tool_choice: str | dict | None = None
    response_format: dict | None = None
    parallel_tool_calls: bool | None = None
```

Canonical formats = the OpenAI shapes (messages, tool schema). Adapters translate
FROM canonical TO native and back. This keeps the OpenAI adapter a near-identity
pass-through and localizes all translation cost in the non-OpenAI adapters.

### 2. Provider protocol — `renglo/agent/llm/base.py`

```python
class LLMProvider(Protocol):
    def complete(self, req: LLMRequest) -> LLMMessage: ...
    # Optional, add when needed:
    # def stream(self, req: LLMRequest) -> Iterator[StreamChunk]: ...
```

### 3. Adapters — `renglo/agent/llm/adapters/`

- `openai_adapter.py` — wraps `get_client(model)` (reuse `client.py`). Nearly
  identity: build params from `LLMRequest`, call `chat.completions.create`, map
  `choices[0].message` → `LLMMessage`. Reference implementation.
- `gemini_adapter.py` — native `google-genai`. Translations required:
  - messages: OpenAI `messages` (incl. a `system` message) → Gemini `contents`
    with roles `user`/`model`, **system pulled out** into `system_instruction`.
  - tools: OpenAI `tools[].function` → Gemini `function_declarations`;
    `tool_choice` → `tool_config`.
  - response: `candidates[0].content.parts` → `LLMMessage`; function-call parts
    → `ToolCall` (serialize args to JSON string to match canonical form).
- `anthropic_adapter.py` (optional, when needed) — `anthropic` SDK: `system`
  param, `tools` schema, `content` blocks / `tool_use` blocks.

Each adapter is ~1 file, self-contained, individually testable with a recorded
fixture (no live calls in unit tests).

### 4. Factory — extend `client.py` (or new `provider.py`)

```python
def provider_for(model: str) -> LLMProvider:
    # gemini-*/gemma-* WITHOUT compatibility bet -> GeminiAdapter
    # claude-*/anthropic.* -> AnthropicAdapter
    # else -> OpenAIAdapter (covers OpenAI + all compatible endpoints)
```

Decide per model whether to use the native adapter or the OpenAI-compatible path.
Recommendation: keep Gemini on the **compatible** path (OpenAIAdapter) by default
since it works today; only route to `GeminiAdapter` when you specifically need a
native-only feature. Make it a small allowlist/config, not automatic.

### 5. Migrate call sites

Replace the ~17 wired sites:

```python
# from
response = get_client(params['model']).chat.completions.create(**params)
resp = response.choices[0].message
# to
resp = provider_for(params['model']).complete(LLMRequest(**params))   # -> LLMMessage
```

Downstream code then reads `resp.content` / `resp.tool_calls` (neutral) instead
of the OpenAI object. **Audit every reader of `.choices` / `.tool_calls[].function`
and migrate it to the DTO** — this is the real work and the main risk. Do it
handler-by-handler, not in one sweep.

Also migrate the Responses-API sites: add a `respond()`/structured-output method
to the protocol (or fold into `complete` via `response_format`) so
`add_flight_rextur`, `llm_responses`, `react_utilities` stop calling
`.responses.*` directly.

## Suggested phasing

1. DTOs + protocol + `OpenAIAdapter` + `provider_for` returning only the OpenAI
   adapter. Migrate 2–3 handlers to `provider_for(...).complete(...)`. Prove the
   DTO carries everything (esp. tool calls) with zero behavior change.
2. Migrate remaining `chat.completions` handlers to the DTO. Delete now-unused
   per-handler `self.AI_1 = OpenAI(...)` construction.
3. Add `GeminiAdapter`; add a golden test that the SAME `LLMRequest` produces an
   equivalent `LLMMessage` (incl. a tool call) through OpenAI and Gemini adapters.
4. Migrate Responses-API sites.
5. (Optional) `AnthropicAdapter` / `BedrockAdapter`, `stream()`.

## Testing

- Unit-test each adapter's translate-in / translate-out with recorded fixtures;
  no network.
- Cross-adapter contract test: one `LLMRequest` with tools → assert both adapters
  return an `LLMMessage` with a populated `ToolCall`.
- Keep `test_model_config.py` / `test_client.py` green.
- Manually drive the agent (`agent_react` path via `AgentUtilities`) once per
  phase — tool calling is where providers diverge; that is the thing to watch.

## Non-goals / keep as-is

- `agent_react.py` model constants stay local (see registry decision).
- `model_config.py` role model — the adapter layer consumes it, does not replace
  it.
- Do not auto-route Gemini to the native adapter; the compatible endpoint is the
  cheaper default. Native adapter is opt-in per model/feature.
