# CLAUDE.md

Instructions for Claude working in this repository. Read this file first, then the
detailed guides in `.claude/docs/` before touching any code.

## Product

A Telegram bot for a user who writes history posts for X. The user sends a topic. The bot
gathers facts from sources (Wikipedia ru/en, Tavily web search) and writes a post or a thread in
Russian. The user reviews it and publishes it by hand. The bot never touches X.

The main risk is that an LLM invents dates, numbers and quotes. So the pipeline is
"research -> facts with sources -> post written only from facts", never "request -> post".
The second goal is text that does not read like AI slop: a deterministic style filter, an LLM
critic pass and few-shot examples written by the user.

Two halves, with different risk profiles:

- **Factual integrity.** Every claim in a post traces to a fact, every fact carries a verbatim
  quote from a source snippet, and code (not the model) checks both links.
- **Voice.** The post must keep one consistent voice, set by the user's reference posts. Style is
  enforced by deterministic
  checks, a critic pass and few-shot examples. See
  [`.claude/docs/style-rules.md`](.claude/docs/style-rules.md).

## Locked architectural decisions

Do not revisit these without an explicit instruction from the user.

| Decision          | Choice                                                                                                                         | Consequence                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| Users             | One user. Access by a whitelist of Telegram IDs.                                                                               | No multi-user code, no accounts, no per-user settings. Messages from any other ID are ignored.                      |
| Publishing to X   | The bot never publishes. There is no X client code.                                                                            | Do not add an X API dependency, token or module. The user publishes by hand.                                        |
| Source of text    | A post is written only from a list of facts with sources.                                                                      | The writing step receives facts, never raw snippets. The topic is a frame, not a source. Numbers and dates are verified against facts. |
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

The skeleton from HIS-2 exists: `app/main.py`, `app/config/`, `app/bot/` and `tests/`. HIS-3
added the LLM client in `app/llm` with its models in `app/domain/llm.py` and the JSON reply prompt
in `app/prompts/json_reply.py`. HIS-4 added the research sources in `app/research` and, in
`app/services`, query planning and the research orchestrator. HIS-5 added fact extraction in
`app/services/facts.py` with the quote check and the source domain rules beside it, and its models
in `app/domain/fact.py`. HIS-6 added the writing step in `app/services/generator.py`, the few-shot
loader in `app/services/style.py`, the `Draft` models in `app/domain/draft.py`, the style rule data
in `app/config/style.py` and the prompts in `app/prompts/writing.py` and
`app/prompts/style_rules.py`. HIS-22 added the short post rules (fact selection, sentence budget,
retry wording, optional tail drop) in `app/services/short_post.py`. HIS-23 added
`app/services/disputes.py`, which adds the disputed facts a draft states to `used_fact_ids`. HIS-7
added the style filter: the deterministic checks in `app/services/style_filter.py`, the critic in
`app/services/style_critic.py`, the regeneration loop in `app/services/style_review.py`, the models
in `app/domain/style.py`, the critic and revision prompts in `app/prompts/style_critique.py`, and
the matching data next to the rules in `app/config/style.py`. HIS-8 connected the pipeline to Telegram:
the orchestration in `app/services/pipeline.py`, the state behind the `RunStore` Protocol in
`app/services/run_store.py`, the outcome models in `app/domain/pipeline.py`, the button revisions
and angles in `app/prompts/revisions.py`, and in `app/bot` the handlers, the background jobs
(`jobs.py`, `flow.py`), the progress message, the input parsing (`requests.py`), the message
formatting (`formatting.py`), the keyboard and all the Russian texts (`messages.py`). HIS-28
added the weak domains and the per-domain cap: `SourceRef.weak`, `is_weak_source` in
`app/services/source_domain.py` and the selection rules in `app/services/fact_selection.py`. HIS-32
added the stance of a claim: `ClaimStance` and `Fact.stance`/`rebutted_by` in `app/domain/fact.py`, the
marker check in `app/services/stance.py` with its data in `app/config/stance.py`, and the attributed
claims block of the writer and critic prompts. HIS-27 added the relevance ranking of the facts: the
pass in `app/services/facts.py`, its models and the period check in `app/services/fact_relevance.py`,
the order by relevance and aspect in `app/services/fact_selection.py` and the prompt in
`app/prompts/fact_relevance.py`. HIS-43 added quantity words (shares, multiples, «полтора») to
both number checks: the matching and the share-sum guard in `app/services/quantities.py`, the forms
in `app/config/quantities.py` and `Draft.unverified_share_sets`. `db` does not exist yet.

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
9. **Never run git commands that write.** No commit, no push, no branch, no PR. The owner does
   that after review.
10. **Anything found outside the task is not fixed in the same branch.** Describe it in the
    report so the owner can open a separate ticket.

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
