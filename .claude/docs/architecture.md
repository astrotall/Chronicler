# Architecture

This document is the authority on where a file belongs, what it may import, and the two
interfaces the pipeline stands on: the LLM client and the research source.

## Layers

| Layer      | Directory       | Holds                                                                        |
| ---------- | --------------- | ---------------------------------------------------------------------------- |
| bot        | `app/bot`       | aiogram handlers, keyboards, whitelist check, message formatting. No logic.  |
| services   | `app/services`  | Pipeline steps and the orchestration between them: planning, facts, generator, style |
| llm        | `app/llm`       | The LLM client interface, provider clients, the per-step provider router     |
| research   | `app/research`  | The research source interface and source clients (Wikipedia, Tavily)         |
| prompts    | `app/prompts`   | Prompt templates and their rendering. Text only, no calls                    |
| domain     | `app/domain`    | Pydantic models shared across layers: `Snippet`, `Fact`, `Draft` and so on   |
| config     | `app/config`    | Settings (pydantic-settings) and constants: stop phrases, URLs, model names  |
| db         | `app/db`        | Appears later. SQLite persistence                                            |

## Import direction

```
bot  ->  services  ->  llm
                   ->  research
```

1. **Downward only.** `bot` may import `services`. `services` may import `llm` and `research`.
   Nothing imports upward: `llm` and `research` never import `services`, and `services` never
   imports `bot`.
2. **`llm` and `research` do not import each other.** If both need something, it belongs in
   `domain` or `config`.
3. **`domain`, `config` and `prompts` are leaves.** Any layer may import them. They import
   nothing from the layers above. `prompts` may import `domain`; `domain` imports only
   Pydantic and the standard library.
4. **`db`, when it exists,** is reached from `services` through an interface defined in
   `services`, never imported by `llm` or `research`.
5. **`bot` holds no business logic.** A handler parses input, calls one service function and
   formats the result.
6. **`app/main.py` is the composition root.** It may import any layer to wire the application
   and check its configuration at startup. Nothing imports it.

The direction is not checked by tooling yet. It is enforced by review.

## Where does this file go?

Answer in order; the first match wins.

1. Does it talk to Telegram (handler, keyboard, message text)? -> `bot`
2. Does it call an LLM provider API? -> `llm`
3. Does it call a source of facts (Wikipedia, Tavily)? -> `research`
4. Is it the text of a prompt? -> `prompts`
5. Is it a data shape passed between layers? -> `domain`
6. Is it a setting, a URL, a model name or a stop phrase? -> `config`
7. Does it decide something about the pipeline (verify a quote, check style, assemble a draft)?
   -> `services`

## LLM client

One interface, two providers. Services depend on the interface, never on a provider.

```python
class LLMClient(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult: ...

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T: ...
```

The protocol is in `app/llm/client.py`. `Message` (a `Role` and the text), `LLMUsage` and
`LLMResult` are in `app/domain/llm.py`, because prompts build messages and services read
results.

- `complete` returns an `LLMResult`: the text, the token usage (input and output) and
  `truncated`, which is true when the reply hit `max_tokens`. An empty reply raises
  `LLMInvalidResponseError`. Used by the writing step.
- `complete_json` returns an instance of `schema` or raises `LLMInvalidResponseError`. It never
  returns a partially valid object, and the raw invalid reply never leaves `app/llm`, not even
  in the error or the log. Used by query planning, fact extraction and the critic.
  - A system message with the JSON Schema of `schema` goes after the caller's leading system
    messages. DeepSeek also gets `response_format: json_object`. Anthropic gets only the
    instruction: `output_config.format` accepts a subset of JSON Schema, and an unsupported
    keyword would turn into a 400 with no retry.
  - The reply is read from a fenced block (```` ```json ... ``` ```` or a bare fence) anywhere
    in the text, otherwise from the whole text, and validated with `schema`.
  - If the reply is invalid, the client sends it back as an `assistant` turn followed by a
    `user` turn that lists the validation problems (field path and message, never input
    values). If the reply was truncated, that turn also asks for a shorter output. An empty
    reply (DeepSeek's JSON mode sometimes returns one) is retried unchanged. Every attempt,
    including an empty one, counts against `LLM_JSON_MAX_RETRIES`.
- `max_tokens` is required: Anthropic needs it, and with thinking on it also caps the
  reasoning, so a step must budget for it.
- `temperature` is optional and sent only where the model accepts it. Current Claude models
  (after Opus 4.6) reject any value other than 1.0, so the Anthropic client never sends it.
  DeepSeek sends it unless thinking mode is on (`DEEPSEEK_THINKING`), which does not support it.
- Provider-specific details (headers, JSON mode, message format, system prompt placement,
  thinking blocks) stay inside `app/llm`. A service never sees them.
- Implementations: `DeepSeekClient` (OpenAI-compatible chat completions) and `AnthropicClient`
  (Messages API, system prompt as the top-level `system` field, `thinking` blocks skipped). Both
  are plain `httpx.AsyncClient` clients, with no SDK. Request and response bodies are Pydantic
  models.

### Reliability and errors

`RetryingTransport` (`app/llm/transport.py`) sends every request.

- Retried with exponential backoff (`LLM_RETRY_BASE_DELAY_SECONDS * 2^n`, capped at
  `LLM_RETRY_MAX_DELAY_SECONDS`), up to `LLM_MAX_RETRIES`: network errors, 429 and 5xx
  (including Anthropic's 529).
- `Retry-After` (seconds or an HTTP date) replaces the backoff. If it asks for longer than the
  cap, the client fails at once instead of blocking the bot.
- Each attempt has a hard deadline, `LLM_ATTEMPT_TIMEOUT_SECONDS`. Under load DeepSeek keeps the
  connection open and sends blank lines for up to 10 minutes, so httpx's read timeout never
  fires. A missed deadline counts as a network error.
- Other 4xx are not retried.

| Exception                 | When                                                        |
| ------------------------- | ----------------------------------------------------------- |
| `LLMError`                | Base class. Carries the step, the provider and the model    |
| `LLMConfigError`          | A step uses a provider whose API key is not set             |
| `LLMAuthError`            | 401, 403                                                    |
| `LLMRequestError`         | Any other 4xx except 429, and a request with no user message |
| `LLMRateLimitError`       | 429 after the retries, or a `Retry-After` beyond the cap    |
| `LLMUnavailableError`     | 5xx or a network error after the retries                    |
| `LLMInvalidResponseError` | An empty reply, an unexpected response body, or no valid JSON after the attempts |

No `httpx` exception leaves `app/llm`.

Each call is logged once at INFO with the provider, model, step, input and output tokens,
duration and number of retries. Retries and invalid JSON replies are logged at WARNING. Prompt
and reply texts are never logged at any level.

### Provider per step

The provider is chosen in config, separately for each step:

| Step key          | Used by              |
| ----------------- | -------------------- |
| `query_planning`  | step 1               |
| `fact_extraction` | step 3               |
| `writing`         | step 4               |
| `style_critique`  | step 5, critic pass  |

The step keys are the `LLMStep` enum, and the providers are the `LLMProvider` enum, both in
`app/config/constants.py`.

`LLMClientFactory` (`app/llm/factory.py`) maps a step to a configured client. A service calls
`get_client(step)` for its own step and receives an `LLMClient`. All clients share one
`httpx.AsyncClient`, built by `build_http_client(settings)`. The default for every step is
DeepSeek. Moving one step to Anthropic is a config change and touches no service code:
`LLM_<STEP>_PROVIDER` picks the provider, and `LLM_<STEP>_MODEL` optionally overrides the
provider's default model (`DEEPSEEK_MODEL`, `ANTHROPIC_MODEL`).

Settings are a Pydantic model (pydantic-settings), validated at startup. A missing key for a
provider that a step uses is a startup error, not a runtime surprise: `app/main.py` calls
`validate_provider_keys` right after loading settings and stops with the name of the missing
variable. A provider that no step uses needs no key. The factory runs the same check when it is
built.

## Research source

```python
class ResearchSource(Protocol):
    name: str

    async def search(self, query: str) -> list[Snippet]: ...
```

- A source takes one query and returns snippets: text, URL, title, language.
- A source validates the provider's response with Pydantic at the boundary and returns only
  `Snippet` objects. Provider JSON never leaves `app/research`.
- A source raises a typed error on failure. The service that fans out to sources decides to skip
  it (see [pipeline.md](pipeline.md), "Failure behaviour").
- Starting sources: Wikipedia ru, Wikipedia en (one class parameterised by language) and Tavily.
  A new source is a new class and a config entry, with no change to the pipeline.

## Configuration

- All settings come from one Pydantic settings model, filled from environment variables and an
  `.env` file (never committed).
- The model holds: the Telegram token, the whitelist of Telegram IDs, provider keys, the provider
  per step, model names, source URLs, retry and attempt limits, length limits, the banned-phrase
  list, the number of few-shot examples.
- Nothing in the list above is a literal inside logic.

## Prompts

- Templates live in `app/prompts/`, one module per step, separate from the services that use
  them.
- A prompt module exposes a function that takes domain models and returns messages. It makes no
  network calls.
- The writing prompt omits the few-shot block when the example set is empty.

## Testing approach

- `pytest` with `pytest-asyncio`. `respx` mocks HTTP for source clients and provider clients.
- Services are tested against a fake `LLMClient` and a fake `ResearchSource`, so no test touches
  the network or a real model.
- The verifiers (quote check, number and date check, style filter) are pure functions and are
  tested hardest, including their boundary cases.
