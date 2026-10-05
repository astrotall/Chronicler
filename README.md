# Chronicler

<div align="center">

![Python](https://img.shields.io/badge/-Python_3.12-3776AB?logo=python&logoColor=white&style=for-the-badge)
![aiogram](https://img.shields.io/badge/-aiogram_3-2CA5E0?logo=telegram&logoColor=white&style=for-the-badge)
![Pydantic](https://img.shields.io/badge/-Pydantic_v2-E92063?logo=pydantic&logoColor=white&style=for-the-badge)
![httpx](https://img.shields.io/badge/-httpx-1F6FEB?style=for-the-badge)
![uv](https://img.shields.io/badge/-uv-DE5FE9?style=for-the-badge)
![Ruff](https://img.shields.io/badge/-Ruff-D7FF64?logo=ruff&logoColor=black&style=for-the-badge)
![mypy](https://img.shields.io/badge/-mypy_strict-2A6DB2?style=for-the-badge)

</div>

[![CI](https://github.com/astrotall/chronicler/actions/workflows/ci.yml/badge.svg)](https://github.com/astrotall/chronicler/actions/workflows/ci.yml)

**Chronicler is a Telegram bot that turns a history topic into a fact-checked draft of a post or
a thread in Russian; the user reviews the draft and publishes it by hand, and the bot never posts
to any social network.**

## About the project

A language model invents dates, numbers and quotes. So the bot never goes "topic -> post". It
goes "topic -> research -> facts with verbatim quotes -> post written only from those facts", and
deterministic code checks the links in between. The model reads, selects and writes; it is never
trusted to certify its own output.

Key principles:

- **Facts carry verbatim quotes, checked by code.** Every fact has one or more quotes from a source
  snippet. Code checks that each quote really occurs in its snippet after typographic
  normalisation; a fact with no verified quote is dropped.
- **Numbers are checked twice.** Every number of a fact must occur in its quotes, and every number
  of the post must occur among the facts. A number the code cannot match is a violation and the
  post is rewritten.
- **The stance of a source is kept.** A claim a source gives as a legend, a version or a common
  belief is `claimed`; one it rebuts is `rebutted`, with the rebuttal as a separate fact. Code
  checks that the attribution is visible next to the quote. Such claims are never stated as fact.
- **Weak sources and a per-domain cap.** Video platforms, blog hosting, social networks and school
  slide sites are marked weak: they never make a fact `confirmed` and sort lower. One domain may
  hold at most 6 of the 20 facts. `confirmed` needs two independent, not weak domains.
- **Disputed facts are marked.** A second model pass finds contradicting facts; they become
  `disputed`, are written cautiously and are marked "СПОРНО" in the reply.
- **Facts are ranked against the topic.** A third pass scores how well each fact answers the topic
  and names its aspect. Facts about a source (an exhibition, a book, a researcher) and facts dated
  outside the topic's period are set aside, with the period flag checked by code; aspects take turns
  so one aspect does not fill the list.
- **A style critic with regeneration.** Deterministic checks (dashes, banned phrases, emoji,
  hashtags, a closing question, length, numbers) and a model critic (claims the facts do not
  state, ambiguous pronouns, filler, cliches) drive up to 2 rewrites. A rewrite that drops most of
  the text or the facts is rejected, so the post cannot collapse into one line.
- **A human stays in the loop.** The bot delivers a draft, the facts and their links. Nothing is
  published automatically.
- **LLM providers behind one interface.** DeepSeek and Anthropic are plain `httpx` clients behind
  one protocol, and the provider and model are chosen per pipeline step in config.

## Architecture diagram

```
  Telegram message ("тред: Куликовская битва")
        │
        ▼
 ┌──────────────┐   whitelist of Telegram IDs, format prefix, one job per user
 │  app/bot     │
 └──────┬───────┘
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ app/services/pipeline.py                                             │
 │                                                                      │
 │  1. query planning ──► 3-5 queries (LLM: query_planning)             │
 │  2. research ────────► snippets: Wikipedia ru/en, Tavily web search  │
 │  3. facts ───────────► extraction (LLM: fact_extraction)             │
 │                        ├─ code: quote check, number check, stance    │
 │                        ├─ code: status, weak domains, domain cap     │
 │                        ├─ LLM pass: contradictions -> disputed       │
 │                        └─ LLM pass: relevance -> order, set aside    │
 │  4. writing ─────────► draft (LLM: writing), code: length, numbers   │
 │  5. style filter ────► code checks + critic (LLM: style_critique),   │
 │                        up to 2 regenerations, regression guard       │
 └──────┬───────────────────────────────────────────────────────────────┘
        ▼
 Telegram reply: the post (plain text), warnings, the facts with their
 sources and status, buttons: короче · в тред · другой заход · ещё вариант
```

Code layers and the import direction:

```
 bot  ──►  services  ──►  llm        (provider clients: DeepSeek, Anthropic)
                     └─►  research   (sources: Wikipedia, Tavily)

 domain, config, prompts: leaves, importable from any layer
```

`llm` and `research` never import each other or `services`; `services` never imports `bot`.
Details: [`.claude/docs/architecture.md`](.claude/docs/architecture.md).

## Stack

| Layer | Technology |
|---|---|
| Telegram | aiogram 3 (long polling) |
| HTTP | httpx |
| Validation, config | Pydantic v2, pydantic-settings |
| LLM | DeepSeek, Anthropic (plain HTTP, no SDK) |
| Research | Wikipedia API (ru, en), Tavily search API |
| Storage | in memory (SQLite planned) |
| Package manager | uv |
| Code quality | ruff, mypy (strict), pytest, pytest-asyncio, respx |
| CI | GitHub Actions |

## Repository layout

```
app/
  main.py              entry point: builds the clients and services, starts polling
  bot/                 handlers, jobs and timeouts, progress message, formatting, Russian texts
  services/            pipeline steps and the orchestration between them
    query_planning.py  search queries from the topic
    research.py        runs every source, filters domains, dedups snippets
    facts.py           extraction, verification, contradiction and relevance passes
    quote_check.py     quote and number checks
    stance.py          attribution markers next to a quote
    fact_selection.py  trust steps, relevance order, domain cap, limit
    generator.py       writing step: length rules, number check
    style_*.py         deterministic checks, critic, regeneration loop
    pipeline.py        steps 1-5, outcomes, buttons
    run_store.py       state behind a Protocol, in memory for now
  llm/                 LLM client protocol, DeepSeek and Anthropic clients, per-step factory
  research/            research source protocol, Wikipedia and Tavily clients
  prompts/             prompt templates, kept apart from the code that calls them
  domain/              Pydantic models shared by the layers
  config/              settings, constants, style rule data, stance markers
tests/                 unit tests with scripted fakes; live tests marked `integration`
  fixtures/            recorded Wikipedia and Tavily responses for the source tests
data/
  examples/            reference posts for few-shot (local, not tracked)
  comparisons/         output of live test runs (local, not tracked)
.claude/docs/          pipeline, style rules, architecture, decisions
.githooks/pre-commit   make check and make test before every commit
```

## Running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies from uv.lock
cp .env.example .env          # then fill in the values
make setup-hooks              # once per clone: turn on the git hooks
make run                      # start the bot (aiogram polling)
```

### Keys

`.env` holds the secrets and is never committed. A missing or invalid variable stops the start
with a message that names it.

| Variable | Required | Without it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | the bot does not start |
| `OWNER_TELEGRAM_IDS` | yes | the bot does not start; IDs allowed to use the bot, comma-separated |
| `DEEPSEEK_API_KEY` | while any step uses DeepSeek (the default) | the bot does not start |
| `ANTHROPIC_API_KEY` | only if a step is moved to Anthropic | the bot does not start for that config |
| `TAVILY_API_KEY` | no | web search is off, only Wikipedia is searched |
| `WIKIPEDIA_CONTACT` | no | Wikipedia is off. An email or a page URL, sent only in the `User-Agent` header as the Wikimedia policy asks |

With neither Wikipedia nor Tavily enabled, a topic is answered "no source enabled" before any model
call. Messages from any Telegram ID outside the list are ignored without a reply.

### Using the bot

Send a topic as a plain message: `Куликовская битва`. The default format is a short post
(`POST_DEFAULT_FORMAT`). A prefix picks another one: `тред: ...` for a thread, `лонг: ...` for a
long post, `коротко: ...` for a short one. A command other than `/start` and a message without text
get a hint; a message over 500 characters is refused as not a topic.

While it works, one message shows the stage. One request runs at a time per user; a new one in the
meantime is answered "ещё работаю". The reply comes in this order:

1. The post as plain text, ready to copy; a thread as one message per tweet.
2. Warnings, if any: numbers not among the facts, length problems, style violations left after the
   rewrites, a critic failure, a thread turned into a short post for lack of facts, a post that
   leans on weak sources or uses a version or a rebutted claim.
3. The facts with their status ("подтверждён", "один источник", **СПОРНО**, "версия",
   "опровергнуто") and links to their sources, weak ones marked "слабый".

Buttons never search again; they rewrite from the same facts, and every new version goes through
the style filter:

| Button | Effect |
|---|---|
| короче | a shorter version, same format and angle |
| в тред | the same facts as a thread, shown only with enough facts |
| другой заход | another angle (a person, a detail, a place, two facts side by side) |
| ещё вариант | the same angle in other words |

When nothing comes out, the progress message turns into the reason: no source enabled, all sources
down, nothing found, too few facts (with the facts found), or the step that failed.

### Settings

Every setting with its default is in [`.env.example`](.env.example). The main groups:

- **LLM:** `LLM_<STEP>_PROVIDER` and `LLM_<STEP>_MODEL` for the steps `QUERY_PLANNING`,
  `FACT_EXTRACTION`, `WRITING`, `STYLE_CRITIQUE`; `DEEPSEEK_MODEL`, `ANTHROPIC_MODEL`; timeouts and
  retries. Moving a step to another provider is a config change only.
- **Research:** `WIKIPEDIA_MAX_ARTICLES`, `TAVILY_SEARCH_DEPTH` (`basic` costs 1 credit a query, so a
  topic costs up to 5), `TAVILY_MAX_RESULTS`, `RESEARCH_ALLOWED_DOMAINS`,
  `RESEARCH_BLOCKED_DOMAINS`.
- **Facts:** `FACTS_MAX_FACTS` (20), `FACTS_MIN_FACTS` (3), `FACTS_WEAK_DOMAINS`,
  `FACTS_MAX_PER_DOMAIN` (6), `FACTS_DOMAIN_GROUPS`, `FACTS_RELEVANCE_ENABLED` (true).
- **Writing:** `SHORT_MAX_CHARS` (280), `SHORT_MAX_FACTS` (3), `LONG_MIN_CHARS`, `LONG_MAX_CHARS`,
  `THREAD_TWEET_MAX_CHARS`, `THREAD_MAX_TWEETS` (12), `THREAD_MIN_TWEETS` (4),
  `THREAD_MIN_USED_FACTS` (5, not above `THREAD_MIN_FACTS`; 0 turns a minimum off),
  `THREAD_MAX_FACTS` (10, at most this many facts are offered to a thread; not below
  `THREAD_MIN_FACTS`; 0 means no cap), `THREAD_MAX_ATTRIBUTED` (2, extra disputes and legends on top
  of the cap; 0 turns them off), `EXAMPLES_DIR`, `EXAMPLES_MAX`.
- **Style filter:** `STYLE_CRITIC_ENABLED`, `STYLE_MAX_REGENERATIONS` (2), the regression ratios,
  `STYLE_FRAGMENT_OVERLAP` (0.75, how close a rewritten sentence must be to a flagged one to count
  as the same fragment), `STYLE_DROP_SURVIVING_CLAIMS` (false; when true, code cuts the
  unsupported-claim, filler and cliche sentences the critic still flags in the delivered thread or
  long post; never for short posts).
- **Bot:** `POST_DEFAULT_FORMAT`, `THREAD_MIN_FACTS` (5), `PIPELINE_TIMEOUT_SECONDS` (600),
  `STATE_MAX_RUNS` (20).

Reference posts for few-shot are `.md` files in `EXAMPLES_DIR`, one post per file. The folder may be
empty: the post is then written without examples.

## Testing

```bash
make test        # uv run pytest
```

Unit tests never touch the network. Services run against a scripted fake LLM client
(`tests/llm_helpers.py`) and fake sources; HTTP clients are tested with `respx` and recorded
responses in `tests/fixtures`; the bot is tested through `Dispatcher.feed_update`.

Live tests call real APIs and are marked `integration`. `make test` excludes them (`addopts = -m
'not integration'` in `pyproject.toml`). They need keys in the environment or `.env` and are skipped
without them:

```bash
uv run pytest -m integration                                   # all live tests
uv run pytest tests/test_pipeline_live.py -m integration       # one of them
```

| Live test | Needs | Cost |
|---|---|---|
| `test_llm_live.py` | a provider key | one short request per provider with a key |
| `test_research_live.py` | nothing for Wikipedia; `TAVILY_API_KEY` for its Tavily test | 1 Tavily credit |
| `test_facts_live.py`, `test_generator_live.py`, `test_long_live.py`, `test_style_live.py` | `DEEPSEEK_API_KEY` | DeepSeek calls only |
| `test_pipeline_live.py`, `test_domain_trust_live.py` | DeepSeek, Tavily, `WIKIPEDIA_CONTACT` | up to 10 Tavily credits |
| `test_stance_live.py`, `test_relevance_live.py` | DeepSeek; reuse research recorded in `data/comparisons` | DeepSeek calls only when the recording exists |

Their reports go to `data/comparisons/`, which is not tracked.

## Linters, types and git hooks

```bash
make check       # ruff format --check, ruff check, mypy (strict, app and tests)
make format      # ruff format and ruff check --fix
make setup-hooks # git config core.hooksPath .githooks
```

mypy runs in strict mode with the Pydantic plugin, and there is no `Any`. After `make setup-hooks`,
`.githooks/pre-commit` runs `make check` and then `make test` and blocks the commit on a failure.
It changes and stages nothing and makes no network calls. `git commit --no-verify` skips it, but
CI runs the same commands.

## What CI runs

`.github/workflows/ci.yml` runs on every pull request, with read-only permissions, as two jobs on
`ubuntu-latest`. Each checks out the code, installs Python 3.12 with `astral-sh/setup-uv`, runs
`uv sync --locked`, and then:

- `check`: `make check`
- `test`: `make test`

Live tests never run in CI.

## Status

- ✅ the whole pipeline from a Telegram message to a post with its facts and sources;
- ✅ quote, number and stance checks by code; `confirmed`, `single`, `disputed` statuses;
- ✅ weak domains, a per-domain cap and relevance ranking of the facts;
- ✅ short posts, long posts and threads, with length rules and a regression guard;
- ✅ deterministic style checks, a model critic and up to 2 regenerations;
- ✅ DeepSeek and Anthropic behind one interface, provider per step;
- ✅ ruff, mypy strict, unit tests, git hooks and CI;
- ⏳ storage: state lives in memory, a restart loses the facts behind old buttons (SQLite planned);
- ⏳ one user model: access is a whitelist of Telegram IDs, there are no accounts or per-user settings;
- ⏳ the contradiction pass over-reports on sets full of plans and reversals, and a myth a page
  states as its own conclusion is caught only by that pass;
- ⏳ numbers written in words and Roman numerals are not checked; hook, rhythm and thread structure
  are not checked at all;
- ⏳ a fact merged from two different claims can look `confirmed`; there is no positive list of
  trusted domains;
- ⏳ images for posts are not implemented;
- ⚠️ the quality of a draft depends on what the sources return and on the model: the bot prepares a
  draft to review, not a finished text.

Open questions and the reasons behind each decision: [`.claude/docs/decisions.md`](.claude/docs/decisions.md).
The pipeline in detail: [`.claude/docs/pipeline.md`](.claude/docs/pipeline.md). Contributing:
[CONTRIBUTING.md](CONTRIBUTING.md).

Test fixtures contain excerpts from Wikipedia, licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
