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
3. **Extract facts.** The model pulls atomic facts, each with a verbatim quote from a snippet.
   Code checks the quote really occurs in the snippet text; a fact that fails is dropped.
4. **Write.** The model writes the post strictly from the facts. Code checks that every number
   and date in the post appears among the facts.
5. **Filter style.** Deterministic style checks, then an LLM critic pass. On a violation the post
   is regenerated with a note on what to fix, up to 2 attempts.
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

At the moment `app/` holds the skeleton only: settings, an owner-only bot with `/start` and a
stub for plain text. `llm`, `research`, `services`, `prompts` and `domain` are empty packages
that later tickets fill.

## Running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies from uv.lock
cp .env.example .env          # then fill in the values
make run                      # start the bot (aiogram polling)
```

`.env` holds the secrets and is never committed:

| Variable             | Meaning                                                            |
| -------------------- | ------------------------------------------------------------------ |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather                                           |
| `OWNER_TELEGRAM_IDS` | Telegram IDs allowed to use the bot, comma-separated: `123,456`    |
| `LOG_LEVEL`          | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, default `INFO`  |

A missing or invalid variable stops the start with a message naming it. Updates from any other
Telegram ID are ignored without a reply and logged as one line without the message text.

## Before committing

```bash
make check    # ruff format check, ruff lint, mypy strict
make test     # pytest
make format   # ruff format and ruff check --fix, to repair what make check reports
```

The same two commands run in CI on every pull request (`.github/workflows/ci.yml`).

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
