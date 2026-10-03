# CLAUDE.md

Instructions for Claude working in this repository. Read this file first, then the
detailed guides in `.claude/docs/` before touching any code.

## Product

A personal Telegram bot for the author of a history account on X. The author sends a topic.
The bot gathers facts from sources (Wikipedia ru/en, Tavily web search) and writes a post or
a thread in Russian. The author reviews it and publishes it by hand. The bot never touches X.

The main risk is that an LLM invents dates, numbers and quotes. So the pipeline is
"research -> facts with sources -> post written only from facts", never "request -> post".
The second goal is text that does not read like AI slop: a deterministic style filter, an LLM
critic pass and few-shot examples written by the author.

Two halves, with different risk profiles:

- **Factual integrity.** Every claim in a post traces to a fact, every fact carries a verbatim
  quote from a source snippet, and code (not the model) checks both links.
- **Voice.** The post must sound like one specific author. Style is enforced by deterministic
  checks, a critic pass and few-shot examples. See
  [`.claude/docs/style-rules.md`](.claude/docs/style-rules.md).

## Locked architectural decisions

Do not revisit these without an explicit instruction from the user.

| Decision          | Choice                                                                                                                         | Consequence                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| Users             | One user. Access by a whitelist of Telegram IDs.                                                                               | No multi-user code, no accounts, no per-user settings. Messages from any other ID are ignored.                      |
| Publishing to X   | The bot never publishes. There is no X client code.                                                                            | Do not add an X API dependency, token or module. The author publishes by hand.                                      |
| Source of text    | A post is written only from a list of facts with sources.                                                                      | The writing step receives facts, not the raw topic or raw snippets. Numbers and dates are verified against facts.   |
| LLM providers     | Anthropic and DeepSeek behind one interface. The provider is chosen by config separately for each pipeline step. Start with DeepSeek. | Steps: `query_planning`, `fact_extraction`, `writing`, `style_critique`. Switching a step is a config change only.  |
| Storage           | No database first, then SQLite.                                                                                                | Early iterations keep state in memory and in files. `app/db` appears later and uses SQLite.                         |
| Deferred infra    | PostgreSQL, Redis, Arq, Alembic and Docker are not used until a concrete task needs them.                                      | Do not add them, do not write code "ready for" them. Each needs its own ticket with a stated reason.                |
| Fact status       | A fact is `confirmed` (2+ independent domains), `single` or `disputed`.                                                        | A `disputed` fact is written cautiously in the post and marked with the word "СПОРНО" in the bot's reply.           |

## Stack

| Concern             | Tool                                         |
| ------------------- | -------------------------------------------- |
| Language            | Python 3.12                                  |
| Telegram            | aiogram 3                                    |
| HTTP                | httpx                                        |
| Validation, config  | Pydantic v2, pydantic-settings               |
| Package manager     | uv                                           |
| Lint and format     | ruff                                         |
| Types               | mypy, strict mode                            |
| Tests               | pytest, pytest-asyncio, respx                |
| LLM                 | Anthropic and DeepSeek behind one interface  |
| Research            | Wikipedia ru/en, Tavily                      |
| Storage             | none at first, SQLite later                  |

## Repository layout

```
.
├── CLAUDE.md
├── CONTRIBUTING.md
├── README.md
├── .claude/
│   └── docs/                  Pipeline, style, architecture and decision guides
├── app/
│   ├── main.py                Entry point, starts aiogram polling
│   ├── bot/                   aiogram handlers, keyboards, message formatting
│   ├── services/              Pipeline steps: planning, facts, generator, style
│   ├── llm/                   LLM interface and provider clients
│   ├── research/              Research source interface and source clients
│   ├── prompts/               Prompt templates, kept apart from service code
│   ├── domain/                Pydantic models shared across layers
│   ├── config/                Settings and constants
│   └── db/                    Appears later, SQLite
├── tests/
└── data/
    └── examples/              Reference posts for few-shot, may be empty
```

The skeleton from HIS-2 exists: `app/main.py`, `app/config/`, `app/bot/` and `tests/`. `llm`,
`research`, `services`, `prompts` and `domain` are empty packages that later tickets fill. `db`
does not exist yet.

## Detailed guides

Read the guide that covers the area you are about to change. They are normative, not
suggestions.

- [`.claude/docs/pipeline.md`](.claude/docs/pipeline.md): the six steps, the data models, what
  code verifies and what the model does
- [`.claude/docs/style-rules.md`](.claude/docs/style-rules.md): the rules every generated post
  must satisfy, and how they are enforced
- [`.claude/docs/architecture.md`](.claude/docs/architecture.md): layers, import direction, the
  LLM client interface, the research source interface
- [`.claude/docs/decisions.md`](.claude/docs/decisions.md): decisions taken and questions still
  open

## Hard rules

These override any default behaviour.

1. **No comments in source code.** Names carry the meaning. Explanations belong in markdown.
2. **Full type hints.** mypy strict passes. No `Any`.
3. **Pydantic v2 at every external boundary:** LLM responses, source API responses, config.
4. **No magic strings.** Model names, URLs, stop phrases and prompt texts live in config or in
   dedicated modules, never inline in logic.
5. **Prompts live apart from service code,** in `app/prompts/`.
6. **Import direction is `bot -> services -> llm / research`.** Never the other way.
7. **Smallest possible change.** No side refactoring.
8. **No new dependencies** without agreement.
9. **Never run git commands that write.** No commit, no push, no branch, no PR. The author does
   that after review.
10. **Anything found outside the task is not fixed in the same branch.** Describe it in the
    report so the author can open a separate ticket.

## Workflow expectations

Before implementing, estimate the size:

- **Small** (1-2 files, no architectural impact): just do it.
- **Medium** (3-10 files): present the plan first.
- **Large** (10+ files, or any change to a locked decision, a pipeline step, a style rule, or a
  public interface): present current state, proposed solution, affected files and risks, then
  wait for approval.

After implementing, verify: types pass, no duplicated logic, no unrelated files were modified.
Run `make check` (ruff format check, ruff lint, mypy strict) and `make test` (pytest), and report
the real result. CI runs the same two commands on every pull request. If a command was not run,
state plainly that it was not.

When the change adds or alters real behaviour (a verifier, a parser, a filter, a prompt that
feeds a verifier, anything with branches), derive the boundary and failure cases and cover
them with tests: empty input, an empty few-shot set, a quote that almost matches, a number
written in words, an LLM reply that is not valid JSON. Skip this only for changes with no
logic: docs, formatting, renames, pure config.
