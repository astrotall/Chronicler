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
        self, messages: Sequence[Message], *, temperature: float | None = None
    ) -> str: ...

    async def complete_json[T: BaseModel](
        self, messages: Sequence[Message], response_model: type[T]
    ) -> T: ...
```

The signatures show the intent; exact types are fixed in the implementing ticket.

- `complete` returns free text. Used by the writing step.
- `complete_json` returns an instance of the Pydantic model it was given. The client asks the
  provider for JSON, validates the reply against `response_model`, and raises a typed error on a
  malformed reply after a bounded number of retries (a config value). It never returns a
  partially valid object. Used by query planning, fact extraction and the critic.
- Provider-specific details (headers, JSON mode, message format, token limits) stay inside
  `app/llm`. A service never sees them.
- Implementations: an Anthropic client and a DeepSeek client. Both are small `httpx`-based
  clients or the official SDK, decided in the implementing ticket (a new dependency needs
  agreement).

### Provider per step

The provider is chosen in config, separately for each step:

| Step key          | Used by              |
| ----------------- | -------------------- |
| `query_planning`  | step 1               |
| `fact_extraction` | step 3               |
| `writing`         | step 4               |
| `style_critique`  | step 5, critic pass  |

A small router in `app/llm` maps a step key to a configured client. A service asks the router
for its own step and receives an `LLMClient`. The default for every step is DeepSeek. Moving one
step to Anthropic is a config change and touches no service code.

Settings are a Pydantic model (pydantic-settings), validated at startup. A missing key for a
provider that a step uses is a startup error, not a runtime surprise.

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
