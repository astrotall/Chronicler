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
   `services`, never imported by `llm` or `research`. That interface is `RunStore`
   (`app/services/run_store.py`).
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

The protocol is in `app/research/source.py`. `Snippet` and `SnippetOrigin` are in
`app/domain/snippet.py`, because the facts step reads snippets.

- A source takes one query and returns snippets. `Snippet` carries `id`, `origin`, `title`, `url`,
  `text` and `lang`.
  - `url` and `origin` are required. A blank or non-http(s) url fails validation, so a snippet
    with no provenance cannot exist. A source drops a provider result that fails validation and
    logs the count, never the content.
  - `id` is the first 16 hex characters of the sha256 of the normalised url (lowercase scheme and
    host, no fragment, no trailing slash). It is derived in the model, so it is the same between
    runs and survives truncating the text.
  - `lang` is `ru` or `en` for Wikipedia and `None` for Tavily, whose API does not report it.
- A source validates the provider's response with Pydantic at the boundary and returns only
  `Snippet` objects. Provider JSON never leaves `app/research`.
- A source raises a `SourceError` subclass on failure (`app/research/errors.py`):

| Exception                     | When                                                    |
| ----------------------------- | ------------------------------------------------------- |
| `SourceError`                 | Base class. Carries the source name and a reason        |
| `SourceAuthError`             | 401, 403                                                |
| `SourceRateLimitError`        | 429                                                     |
| `SourceRequestError`          | Any other 4xx, or an `error` object in a MediaWiki reply |
| `SourceUnavailableError`      | 5xx or a network error                                  |
| `SourceInvalidResponseError`  | A body that is not the documented shape                 |

  The reason holds the HTTP status or the exception class, never a response body. No `httpx` or
  `ValidationError` leaves `app/research`. Sources do not retry: a failed source is skipped.
- Sources share one `httpx.AsyncClient`, built by `build_research_http_client(settings)`
  (`app/research/http.py`) with the `RESEARCH_*` timeouts. `app/research` does not import
  `app/llm`, so it has its own builder.
- Starting sources: `WikipediaSource` (one class parameterised by language, ru and en) and
  `TavilySource`. `build_sources(settings, client)` in `app/research/factory.py` builds the
  enabled ones: Wikipedia needs `WIKIPEDIA_CONTACT`, Tavily needs `TAVILY_API_KEY`. A blank value
  counts as not set. `log_source_availability(settings)` logs one warning per disabled source,
  without values, and one more if none is enabled. `app/main.py` calls it at startup and does not
  stop. A new source is a new class and a line in the factory, with no change to the orchestrator.

### Research orchestrator

`ResearchService` (`app/services/research.py`) takes the sources and `ResearchLimits` (built from
settings by `ResearchLimits.from_settings`) and returns a `ResearchResult`: `snippets` and
`failures`, both in `app/domain/research.py`.

- Every query goes to every source. Each `source.search` call runs in `asyncio.gather` under a
  `Semaphore(RESEARCH_MAX_CONCURRENCY)`.
- A `SourceError` becomes a `SourceFailure` (source, query, exception class, reason) and is logged
  at WARNING. Any other exception is a bug and propagates. If every source fails, or none finds
  anything, the result is still returned, with an empty `snippets`. Whether that is enough is the
  next step's decision.
- Snippets are then filtered by domain, deduplicated and truncated, in this order:
  1. Domain filter: `RESEARCH_BLOCKED_DOMAINS` drops a host that equals a listed domain or is its
     subdomain. If `RESEARCH_ALLOWED_DOMAINS` is not empty, only matching hosts stay. Blocked wins
     over allowed. Both lists are empty by default. The filter applies to every origin, so an
     allow-list that omits `wikipedia.org` also drops Wikipedia.
  2. Deduplication by normalised url. Of several snippets with one url the longest text stays,
     and on a tie the first. It keeps the place of the first occurrence.
  3. Truncation of `text` to `RESEARCH_SNIPPET_MAX_CHARS`, with trailing whitespace removed.

### Query planning

`plan_queries(client, topic)` (`app/services/query_planning.py`) calls `complete_json` on the
`query_planning` client with the `QueryPlan` schema and returns the queries. `QueryPlan` holds 3 to
5 queries: each is stripped and non-empty, and no two are equal ignoring case. A reply that breaks
this is an invalid reply: the LLM client sends the problems back, and after
`LLM_JSON_MAX_RETRIES` it raises `LLMInvalidResponseError`. `plan_queries` does not catch it, and
it does not catch other LLM errors. The prompt is `app/prompts/query_planning.py`. It asks for
queries in Russian and in English, but code does not check the languages.

### Fact extraction

`extract_facts(client, topic, snippets, limits)` (`app/services/facts.py`) takes the
`fact_extraction` client, the topic, the snippets from research and `FactLimits` (built by
`FactLimits.from_settings`). It returns `FactExtraction`, the union `FactsExtracted |
InsufficientFacts` from `app/domain/fact.py`; "not enough facts" is a result, not an exception.
The full behaviour is in [pipeline.md](pipeline.md), step 3.

| Module                          | Holds                                                                    |
| ------------------------------- | ------------------------------------------------------------------------ |
| `app/services/facts.py`         | The reply schemas (`ExtractedFacts`, `ConflictReport`), the budget, alias mapping, verification, status, dispute pass, limit and the result |
| `app/services/quote_check.py`   | Pure functions: text normalisation, `check_quote`, `extract_numbers`, `numbers_supported` |
| `app/services/source_domain.py` | Pure function `source_domain(url, groups)`; reuses `host_matches` from the research orchestrator |
| `app/prompts/fact_extraction.py`| `render_fact_extraction` and `render_dispute_check`                       |
| `app/domain/fact.py`            | `SourceRef`, `Fact`, `FactStatus`, `Dispute`, `FactSet`, `ExtractionStats`, the two outcomes |

- Two `complete_json` calls on the same client: extraction (`FACT_EXTRACTION_MAX_TOKENS`) and the
  contradiction check (`DISPUTE_CHECK_MAX_TOKENS`). Contradictions are the same kind of mechanical
  reading as extraction, so they share the step and its provider; there is no separate step key.
- Neither call is caught: `LLMError` and `LLMInvalidResponseError` propagate. A blank topic is a
  `ValueError`. No snippets returns `InsufficientFacts` without a call.
- The reply schemas validate shape only. Labels, ids, quotes and numbers are checked by code
  after validation, so a wrong label drops one support item instead of failing the reply.
- The model sees snippet labels `S1`... and fact ids `C1`..., never the snippet hashes. A label is
  matched after trimming spaces and square brackets and ignoring case; anything else is unknown.
- One INFO line per run logs the `ExtractionStats` counters. Each dropped support item is logged
  at DEBUG with its label, snippet id and reason. Texts of snippets, facts, quotes and model
  replies are never logged.

### Writing

`write_draft(client, fact_set, post_format, limits, examples, *, angle, revision)`
(`app/services/generator.py`) takes the `writing` client, a `FactSet`, a `PostFormat`,
`WritingLimits` (built by `WritingLimits.from_settings`), the few-shot examples as strings, an
optional angle and an optional `Revision`. It returns a `Draft` from `app/domain/draft.py`. The
full behaviour is in [pipeline.md](pipeline.md), step 4.

| Module                           | Holds                                                                    |
| -------------------------------- | ------------------------------------------------------------------------ |
| `app/services/generator.py`      | The reply schemas (`SingleReply`, `ThreadReply`), `WritingLimits`, numbering, the length check and its one retry, fact id matching, the number check |
| `app/services/short_post.py`     | Pure functions for a short post: fact selection, the sentence budget, sentence and paragraph boundaries, the sentences to cut, the tail drop and its disputed-number guard |
| `app/services/style.py`          | `load_examples(directory, limit)`: the few-shot loader                   |
| `app/prompts/writing.py`         | `render_writing`, `render_length_correction`, `render_expand_correction` and `render_short_correction` |
| `app/prompts/style_rules.py`     | `render_style_rules`: the Russian rules block, shared with the critic prompt of step 5 |
| `app/config/style.py`            | Style rule data: banned phrases, forbidden dashes, invented-experience phrases, cautious wordings, and the data the style filter matches with (see "Style filter") |
| `app/services/disputes.py`       | `with_disputed_facts`: adds the disputed facts a draft states to `used_fact_ids` |
| `app/domain/draft.py`            | `PostFormat`, `DraftPart`, `LengthIssue`, `LengthViolation`, `LongSize`, `Revision`, `Draft` |

- One `complete_json` call (`WRITING_*_MAX_TOKENS` per format), and one more only when a length
  limit is broken. The second call carries the first reply as an `assistant` turn and the list of
  problems as a `user` turn. It does not use `LLM_JSON_MAX_RETRIES`, which stays for invalid JSON.
- The number check reuses `extract_numbers` from `app/services/quote_check.py`, and id matching
  reuses `normalize_label` from `app/services/facts.py`.
- One method serves every button of step 6: "короче" and "ещё вариант" pass a `Revision` built
  from `Draft.texts`, "другой заход" passes an angle, "в тред" passes `PostFormat.THREAD`.
- The generator does not read files. The caller loads the examples with `load_examples` and
  passes them in. The loader reads `*.md` files of `EXAMPLES_DIR` sorted by name, skips hidden,
  empty and non-UTF-8 files (the last with a WARNING naming the file), and returns the first
  `EXAMPLES_MAX` texts, stripped. A missing directory gives no examples. `EXAMPLES_DIR` is
  relative to the working directory, the repository root under `make run`.
- LLM errors and `LLMInvalidResponseError` propagate. Texts of posts, facts and replies are never
  logged; the INFO line holds counters only.

### Style filter

`review_style(writer, critic, draft, fact_set, writing_limits, style_limits, examples, *, angle,
allow_closing_question)` (`app/services/style_review.py`) takes the `writing` and the
`style_critique` clients, a `Draft` from `write_draft`, its `FactSet`, `WritingLimits`,
`StyleLimits` (built by `StyleLimits.from_settings`), the same examples and angle the draft was
written with, and whether a closing question is allowed. It returns a `StyleResult` from
`app/domain/style.py`. The full behaviour is in [pipeline.md](pipeline.md), step 5.

| Module                            | Holds                                                                   |
| --------------------------------- | ----------------------------------------------------------------------- |
| `app/services/style_filter.py`    | Pure functions: `check_draft` and one check per deterministic rule, the phrase matcher (`phrase_pattern`, `find_phrases`) |
| `app/services/style_critic.py`    | The reply schema (`CriticReply`), `critique_draft`, the excerpt check against the draft |
| `app/services/style_review.py`    | `StyleLimits`, the evaluation of a version, the regeneration loop, the choice of the best version |
| `app/prompts/style_critique.py`   | `render_critique`, `render_style_revision`, the Russian explanation templates of code violations |
| `app/domain/style.py`             | `StyleRule`, `ViolationSource`, `CriticStatus`, `Violation`, `StyleReport`, `StyleResult` |
| `app/config/style.py`             | Besides the rules: `PHRASE_PLACEHOLDERS`, `PHRASE_STEM_ENDINGS`, `PHRASE_MIN_STEM_CHARS`, `BANNED_PHRASE_EXACT_WORDS`, `EMOJI_RANGES`, `DANGEROUS_STYLE_RULES` |

- The checks read `app/config/style.py` as a module (`from app.config import style`) at call time,
  so a test that changes the data with `monkeypatch` checks the real path. `DANGEROUS_STYLE_RULES`
  holds rule names as strings, because `config` does not import `domain`.
- A regeneration is a plain `write_draft` call with a `Revision`; the generator knows nothing about
  the filter. The excerpt check reuses `check_quote` and `text_segments` from
  `app/services/quote_check.py`, and the critic prompt reuses the fact and dispute blocks of
  `app/prompts/writing.py`, so the critic sees the facts exactly as the writer does.
- `LLMError` from the critic and from a regeneration is caught in `style_review.py`, logged at
  WARNING with the class name only, and turned into `CriticStatus.FAILED` or
  `regeneration_failed`. Other exceptions propagate.
- Texts of posts, facts, excerpts, explanations and replies are never logged; the INFO line holds
  counters and rule names.

### Pipeline and delivery

`Pipeline` (`app/services/pipeline.py`) runs steps 1-5 for a topic (`run_topic`) and steps 4-5 for
a button (`rework`); the full behaviour is in [pipeline.md](pipeline.md), step 6. It takes
`PipelineClients` (the four step clients), a `Researcher` (a Protocol that `ResearchService`
satisfies: `source_names` and `research(queries)`), a `RunStore` and `PipelineLimits` (built by
`PipelineLimits.from_settings`). It returns typed outcomes from `app/domain/pipeline.py`, never
raises an `LLMError`: the error becomes `StepFailed(stage, kind)`, so `bot` never imports `llm`.
The progress callback is a Protocol taking a `PipelineStage`.

| Module                        | Holds                                                                     |
| ----------------------------- | ------------------------------------------------------------------------- |
| `app/services/pipeline.py`    | `Pipeline`, `PipelineClients`, `PipelineLimits`, the `Progress` and `Researcher` Protocols, the error mapping, the thread threshold, the buttons on offer |
| `app/services/run_store.py`   | `RunStore` (Protocol, async) and `InMemoryRunStore`                       |
| `app/prompts/revisions.py`    | `ANGLES` and the `Revision` instruction of each button                    |
| `app/domain/pipeline.py`      | `PipelineStage`, `PostAction`, `FailureKind`, `StoredDraft`, `PostReady` and the other outcomes |
| `app/bot/handlers.py`         | The router: `/start`, a hint for other commands and non-text messages, the topic, the buttons. Each handler parses input and starts one job |
| `app/bot/requests.py`         | `parse_topic`: the format prefix, the empty and over-long topic          |
| `app/bot/flow.py`             | `BotContext`, the jobs for a topic and a button: progress, timeout, delivery, `send` with one `RetryAfter` retry |
| `app/bot/jobs.py`             | `JobRunner`: one background task per user, the busy check, waiting and cancelling |
| `app/bot/progress.py`         | `ProgressMessage`: one message edited per stage, deleted or turned into the final status |
| `app/bot/formatting.py`       | Rendering of outcomes into messages: the post, warnings and facts (HTML, escaped), splitting at 4096 |
| `app/bot/keyboards.py`        | `DraftCallback` (aiogram `CallbackData`) and the keyboard                 |
| `app/bot/messages.py`         | Every Russian text the bot sends, the format prefixes and the labels      |

- `app/main.py` builds everything: the two `httpx` clients, `LLMClientFactory`, the sources,
  `ResearchService`, `InMemoryRunStore`, `Pipeline`, and the dispatcher with `BotContext` in its
  workflow data under `context`, so aiogram passes it to the handlers. Polling runs with
  `handle_as_tasks=True`; on shutdown the running jobs are cancelled and the clients closed.
- `bot` imports `services` (the pipeline, `split_sentences` and `PARAGRAPH_BREAK` for splitting),
  `domain` and `prompts` (`disputed_ids`), never `llm` or `research`.
- Formatting is in `bot` because it is Telegram's: the 4096 limit, HTML escaping, the keyboard.

## Configuration

- All settings come from one Pydantic settings model, filled from environment variables and an
  `.env` file (never committed).
- The model holds: the Telegram token, the whitelist of Telegram IDs, provider keys, the provider
  per step, model names, source URLs, retry and attempt limits, length limits, the examples
  directory and the number of few-shot examples.
- The style filter settings are `STYLE_CRITIC_ENABLED`, `STYLE_MAX_REGENERATIONS` and
  `STYLE_CRITIC_MAX_FINDINGS`. The bot settings are `POST_DEFAULT_FORMAT`, `THREAD_MIN_FACTS`,
  `PIPELINE_TIMEOUT_SECONDS` and `STATE_MAX_RUNS`. The format is a plain literal in config and
  becomes `PostFormat` in `PipelineLimits`, because `config` does not import `domain`. Telegram's
  limits (4096 characters, the topic limit, drafts per run) are constants. The critic's schema limits (explanation and excerpt length) are
  constants in `app/config/constants.py`, because a Pydantic reply schema is static.
- The banned-phrase list and the other style rule data are constants in `app/config/style.py`,
  not environment variables: a phrase such as "это не просто X, а Y" contains the comma that a
  list in one variable would split on.
- Nothing in the list above is a literal inside logic.

## Prompts

- Templates live in `app/prompts/`, one module per step, separate from the services that use
  them.
- A prompt module exposes a function that takes domain models and returns messages. It makes no
  network calls.
- The writing prompt omits the few-shot block when the example set is empty.
- Structure and service instructions are in English. The style rules block shown to the writer
  is in Russian, rendered from `app/config/style.py`.

## Testing approach

- `pytest` with `pytest-asyncio`. `respx` mocks HTTP for source clients and provider clients.
- Services are tested against a fake `LLMClient` and a fake `ResearchSource`, so no test touches
  the network or a real model.
- The verifiers (quote check, number check, source domain, style filter) are pure functions and
  are tested hardest, including their boundary cases.
- Services use a scripted fake client that returns its replies in order and records the calls
  (`ScriptedLLMClient` in `tests/llm_helpers.py`).
- The bot is tested through `Dispatcher.feed_update` with `Bot.__call__` replaced by a recorder
  that returns real `Message` objects (`tests/test_bot_flow.py`), on a `Pipeline` built from
  scripted clients and fake sources (`tests/pipeline_helpers.py`).
