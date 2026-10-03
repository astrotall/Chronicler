# Chronicler

A personal Telegram bot for the author of a history account on X. You send a topic. The bot
researches it (Wikipedia ru/en, Tavily web search), pulls out atomic facts with sources, and
writes a post or a thread in Russian using only those facts. The bot never publishes to X: you
read the draft, check the sources under it, and post it yourself.

The point of the design is that an LLM invents dates, numbers and quotes. So the bot does not go
"request -> post". It goes "research -> facts with sources -> post only from facts", and code
verifies the links in between.

## Pipeline

1. **Plan queries.** From the topic the model produces 3-5 search queries, in Russian and
   English.
2. **Research.** Sources return snippets for each query.
3. **Extract facts.** The model pulls atomic facts in Russian, each with verbatim quotes from
   the snippets. Code checks every quote really occurs in its snippet and every number of a fact
   occurs in its quotes; what fails is dropped. Code marks a fact `confirmed` (2+ independent
   domains) or `single`, a second model pass finds contradictions, and those facts become
   `disputed`. Too few facts is a result shown to the author, not a guess.
4. **Write.** The model writes a short post, a long post or a thread strictly from the facts; the
   topic only frames the post, and the post adds no conclusion or claim of importance the facts
   do not state. Disputed facts are shown to the model apart and written cautiously. Code checks
   the length (a retry, then the draft is marked; a short post is written from 3 facts picked by
   code and is cut only if `SHORT_DROP_TAIL` is on) and warns about every number in the post
   that is not among the facts.
5. **Filter style.** Deterministic checks (dashes, banned phrases, invented experience, emoji,
   hashtags, a closing question, length, numbers not among the facts), then an LLM critic that
   reads the post against the facts (claims the facts do not state, ambiguous pronouns, filler
   lines, cliches, rhetorical triplets, extra opinions). On a violation the post is regenerated
   with the exact list of what to fix, up to 2 times; the best version goes out with whatever
   violations are left. A critic failure never loses the post, it is flagged.
6. **Deliver.** Telegram receives the post, the facts with their sources underneath, and the
   buttons "короче", "в тред", "другой заход", "ещё вариант".

Details: [`.claude/docs/pipeline.md`](.claude/docs/pipeline.md).

## Structure

```
app/
  bot/              aiogram handlers, keyboards, message formatting
  llm/              LLM interface, Anthropic and DeepSeek clients
  research/         research source interface, Wikipedia and Tavily clients
  services/         pipeline steps: planning, facts, generator, style
  prompts/          prompt templates
  domain/           Pydantic models
  config/           settings and constants
  db/               appears later, SQLite
  main.py           entry point, starts aiogram polling
tests/
data/
  examples/         reference posts for few-shot (may be empty)
.claude/
  docs/             pipeline, style rules, architecture, decisions
```

At the moment `app/` holds settings, an owner-only bot with `/start` and a stub for plain text,
the LLM client (`app/llm`, DeepSeek and Anthropic), the research sources (`app/research`,
Wikipedia and Tavily), query planning, the research orchestrator and fact extraction
(`app/services`), the writing step with the few-shot loader, and the style filter with the critic
and the regeneration loop. The bot does not run the pipeline yet.

## Running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies from uv.lock
cp .env.example .env          # then fill in the values
make setup-hooks              # once after cloning: turn on the git hooks
make run                      # start the bot (aiogram polling)
```

`make setup-hooks` points git at `.githooks/`, so every `git commit` first runs `make check` and
`make test` and is blocked if either fails. It is a per-clone setting, so run it once after each
clone.

`.env` holds the secrets and is never committed:

| Variable             | Meaning                                                            |
| -------------------- | ------------------------------------------------------------------ |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather                                           |
| `OWNER_TELEGRAM_IDS` | Telegram IDs allowed to use the bot, comma-separated: `123,456`    |
| `LOG_LEVEL`          | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, default `INFO`  |

A missing or invalid variable stops the start with a message naming it. Updates from any other
Telegram ID are ignored without a reply and logged as one line without the message text.

### LLM providers

The bot calls a model on four pipeline steps: `query_planning`, `fact_extraction`, `writing`,
`style_critique`. Each step has its own provider and model. By default every step runs on
DeepSeek, so only `DEEPSEEK_API_KEY` is required.

| Variable                       | Meaning                                                              |
| ------------------------------ | -------------------------------------------------------------------- |
| `DEEPSEEK_API_KEY`             | DeepSeek key. Required while any step uses DeepSeek                  |
| `ANTHROPIC_API_KEY`            | Anthropic key. Required only if some step uses Anthropic             |
| `DEEPSEEK_MODEL`               | DeepSeek model for steps without their own model, `deepseek-flash`   |
| `ANTHROPIC_MODEL`              | Anthropic model for steps without their own model, `claude-sonnet-5-5` |
| `DEEPSEEK_THINKING`            | `true` turns on DeepSeek thinking mode, default `false`              |
| `LLM_<STEP>_PROVIDER`          | `deepseek` or `anthropic`, for example `LLM_WRITING_PROVIDER`        |
| `LLM_<STEP>_MODEL`             | Model for that step; empty means the provider's model above          |
| `LLM_CONNECT_TIMEOUT_SECONDS`, `LLM_READ_TIMEOUT_SECONDS` | HTTP timeouts                             |
| `LLM_ATTEMPT_TIMEOUT_SECONDS`  | Hard deadline for one request attempt, 300 by default                |
| `LLM_MAX_RETRIES`              | Retries on network errors, 429 and 5xx, 3 by default                 |
| `LLM_RETRY_BASE_DELAY_SECONDS`, `LLM_RETRY_MAX_DELAY_SECONDS` | Exponential pause between retries, and its cap |
| `LLM_JSON_MAX_RETRIES`         | Extra attempts when a structured reply is not valid JSON, 2 by default |

`DEEPSEEK_BASE_URL`, `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_VERSION` are in `.env.example` with
their official values and normally stay as they are.

To move the writing step to Anthropic, add two lines to `.env` and restart:

```bash
LLM_WRITING_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Add `LLM_WRITING_MODEL=claude-opus-5-5` to pick a model other than `ANTHROPIC_MODEL`. No code
changes. If a step uses a provider whose key is missing, the bot stops at startup and names the
variable.

Live tests send a short real request to each provider whose key is set (from the environment or
`.env`); `make test` never runs them:

```bash
uv run pytest -m integration
```

### Research sources

The research step searches Wikipedia ru, Wikipedia en and Tavily. Each source is optional and is
switched on by its own setting. A source whose setting is empty (or only spaces) is off, the bot
logs a warning naming the variable, and the other sources keep working.

| Variable                       | Meaning                                                              |
| ------------------------------ | -------------------------------------------------------------------- |
| `WIKIPEDIA_CONTACT`            | Your email or a page URL. Empty means Wikipedia is off              |
| `TAVILY_API_KEY`               | Tavily key. Empty means Tavily is off                                |
| `WIKIPEDIA_MAX_ARTICLES`       | Articles read per query and edition, 2 by default                    |
| `WIKIPEDIA_EXTRACT_MAX_CHARS`  | Length of one article's text, 6000 by default                        |
| `TAVILY_SEARCH_DEPTH`          | `basic` (1 credit, default), `advanced` (2 credits), `fast`, `ultra-fast` |
| `TAVILY_MAX_RESULTS`           | Results per query, 5 by default, at most 20                          |
| `TAVILY_CHUNKS_PER_SOURCE`     | Chunks per page, 1 to 3, 3 by default. Not sent for `ultra-fast`     |
| `RESEARCH_MAX_CONCURRENCY`     | Searches in flight at once, 5 by default                             |
| `RESEARCH_SNIPPET_MAX_CHARS`   | Longest snippet kept, 8000 by default                                |
| `RESEARCH_ALLOWED_DOMAINS`     | Comma-separated. If not empty, only these domains and their subdomains stay |
| `RESEARCH_BLOCKED_DOMAINS`     | Comma-separated. These domains and their subdomains are dropped      |
| `RESEARCH_CONNECT_TIMEOUT_SECONDS`, `RESEARCH_READ_TIMEOUT_SECONDS` | HTTP timeouts            |

**Wikipedia.** It needs no key, but the Wikimedia User-Agent policy asks every client to say who
runs it. Put your email or the URL of a page about you into `WIKIPEDIA_CONTACT`; it is sent in the
`User-Agent` header and nowhere else. Leave it empty and Wikipedia stays off.

**Tavily.** Sign up at [tavily.com](https://tavily.com), copy the key (it starts with `tvly-`) from
the dashboard into `TAVILY_API_KEY`. The free plan has a monthly credit allowance; with the default
`basic` depth one query costs 1 credit, so one topic costs up to 5. Without the key only Wikipedia
works.

The domain lists apply to every source, so an allow-list that does not include `wikipedia.org`
also drops the Wikipedia snippets.

Live tests call Wikipedia (no key needed) and, if `TAVILY_API_KEY` is set, Tavily:

```bash
uv run pytest tests/test_research_live.py -m integration
```

### Fact extraction

| Variable                 | Meaning                                                                   |
| ------------------------ | ------------------------------------------------------------------------- |
| `FACTS_INPUT_MAX_CHARS`  | Total snippet text shown to the model, 60000 by default. Longer snippets are cut to a common cap |
| `FACTS_MIN_QUOTE_CHARS`  | Shortest quote that counts as support, 20 by default                      |
| `FACTS_MAX_FACTS`        | Most facts kept, 20 by default. Disputed facts are kept on top of it      |
| `FACTS_MIN_FACTS`        | Fewest facts that can be stated for a post, 3 by default. Must not exceed `FACTS_MAX_FACTS` |
| `FACTS_DOMAIN_GROUPS`    | Domains counted as one source: members separated by `,`, groups by `;`. Default `wikipedia.org,wikimedia.org,ruwiki.ru,wikiwand.com`. Empty means no groups |

The live test sends two Wikipedia articles from `tests/fixtures` to DeepSeek and checks that
verified facts come out; it needs `DEEPSEEK_API_KEY`:

```bash
uv run pytest tests/test_facts_live.py -m integration -o log_cli=true --log-cli-level=INFO
```

### Writing

| Variable                  | Meaning                                                                  |
| ------------------------- | ------------------------------------------------------------------------ |
| `SHORT_MAX_CHARS`         | Longest short post, 280 by default                                       |
| `SHORT_MAX_FACTS`         | Most facts a short post is written from, picked by code, 3 by default    |
| `SHORT_SENTENCE_CHARS`    | Sentence length in the short post budget, 80 by default: 280 // 80 = 3 sentences |
| `SHORT_LENGTH_RETRIES`    | Retries of an overlong short post, 2 by default (long and thread get 1)  |
| `SHORT_DROP_TAIL`         | `true` drops trailing paragraphs or sentences of a short post still over the limit after the retries; default `false` |
| `LONG_MAX_CHARS`          | Longest long post, 25000 by default (X Premium)                          |
| `THREAD_TWEET_MAX_CHARS`  | Longest tweet of a thread, numbering included, 280 by default            |
| `THREAD_MAX_TWEETS`       | Most tweets in a thread, 12 by default, at least 2                       |
| `THREAD_NUMBERING`        | `true` adds `1/ `, `2/ `... before each tweet, default `false`           |
| `EXAMPLES_DIR`            | Folder with reference posts, `data/examples` by default                  |
| `EXAMPLES_MAX`            | Most reference posts in a prompt, 3 by default; `0` turns them off       |

Reference posts are `.md` files in `EXAMPLES_DIR`, one post per file, taken in name order. The
folder may be empty: the post is then written without examples. Lengths are counted as Python
`len()`; X counts a link as 23 characters and an emoji as 2, so a post with links can be shorter
on X than the count says.

The live test writes 3 short posts and 2 threads per topic from two small hand-made fact sets
with DeepSeek (2 and 2 with the author examples, skipped when `data/examples` is empty), checks
their structure and that every part fits its limit with no tail dropped, and saves the drafts to
`data/comparisons/generator_live_<topic>.json` (not committed); it needs `DEEPSEEK_API_KEY`:

```bash
uv run pytest tests/test_generator_live.py -m integration -o log_cli=true --log-cli-level=INFO
```

### Style filter

| Variable                    | Meaning                                                                |
| --------------------------- | ---------------------------------------------------------------------- |
| `STYLE_CRITIC_ENABLED`      | `false` turns the LLM critic off; the deterministic checks always run, default `true` |
| `STYLE_MAX_REGENERATIONS`   | Most regenerations after a violation, 2 by default; `0` only reports    |
| `STYLE_CRITIC_MAX_FINDINGS` | Most critic findings used per check, 10 by default; extra ones are dropped and counted |

The critic runs on the `style_critique` step (`LLM_STYLE_CRITIQUE_PROVIDER`). The banned phrases,
the invented-experience phrases and the rules that weigh more when the best version is chosen
are data in `app/config/style.py`, not environment variables.

The live test runs the critic on the local HIS-21 drafts in `data/comparisons/` (skipped when
they are missing) and reports which known defects it finds and what it flags on relatively clean
drafts, then runs the whole filter on fresh and on known bad Kulikovo drafts. Results go to
`data/comparisons/his7_*.json`; it needs `DEEPSEEK_API_KEY`:

```bash
uv run pytest tests/test_style_live.py -m integration -o log_cli=true --log-cli-level=INFO
```

## Before committing

```bash
make check    # ruff format check, ruff lint, mypy strict
make test     # pytest
make format   # ruff format and ruff check --fix, to repair what make check reports
```

After `make setup-hooks` the pre-commit hook runs both for you (a few seconds) and blocks the
commit on a failure. It changes and stages nothing and makes no network calls; live
(`integration`) tests never run in it. `git commit --no-verify` skips it, but CI runs the same two
commands on every pull request (`.github/workflows/ci.yml`), so skipping only delays the failure.

Never commit to `main` directly, and never push without asking. Branches, commits and pull
requests, including the Jira `HIS-` key convention, are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Project context

Product overview, locked decisions and hard rules are in [CLAUDE.md](CLAUDE.md), which is also
the index the AI assistant reads. The detailed guides live in [`.claude/docs/`](.claude/docs):

- [pipeline.md](.claude/docs/pipeline.md): steps, data models, what code checks
- [style-rules.md](.claude/docs/style-rules.md): rules for the text of a post
- [architecture.md](.claude/docs/architecture.md): layers, import direction, interfaces
- [decisions.md](.claude/docs/decisions.md): decisions taken and open questions

Keep these in sync with the code.
