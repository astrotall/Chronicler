# Working in this repository

Branches, commits and pull requests.

## Branches

`main` is always releasable: never push to it directly, only through a pull request. Everything
else is a short-lived branch off `main`.

```
<type>/HIS-<number>/<short-dashed-description>
```

The type is the same as the commit type (below). The Jira key is uppercase, exactly as in Jira.
The description is latin, lowercase, 2-5 words that name the task.

```
feat/HIS-12/wikipedia-source
fix/HIS-27/quote-match-whitespace
refactor/HIS-31/llm-client-interface
docs/HIS-1/project-docs
```

The key is mandatory: Jira uses it to attach the branch, its commits and the pull request to the
task and to move its status. A branch with no key never shows up on the task.

A branch lives until it merges and is deleted right after. There is no long-lived `develop`
branch.

## Commits: Conventional Commits

```
<type>(<scope>): <description>

<footer: Jira key>
```

The subject is up to about 72 characters, **in English**, imperative mood, no trailing period.
Type and scope are English too.

Commits are short: the first line and the ticket number in the footer. A body is written only
when the reason for the change is not obvious from the subject.

**Language.** All commit text, branch names and PR titles are English. Documentation in this
repository is English as well. The only Russian text is the example posts.

```
feat(facts): drop a fact whose quote is missing from the snippet

HIS-14
```

The Jira key goes on its own line in the footer, after a blank line. Keep it out of the subject:
the subject is read by people, the footer is read by Jira.

```
feat(research): add the Tavily search source
fix(style): catch the en dash as well as the em dash
refactor(llm): extract the provider factory from the client module
docs(project): add CLAUDE.md, CONTRIBUTING.md and README.md
chore(deps): add respx to the dev group
ci: run ruff and mypy on pull requests
```

### Types

| Type       | When                                                      |
| ---------- | --------------------------------------------------------- |
| `feat`     | new functionality                                         |
| `fix`      | a bug fix                                                 |
| `refactor` | a code change that does not alter behaviour               |
| `perf`     | a performance improvement                                 |
| `style`    | formatting that does not change meaning                   |
| `docs`     | documentation, including `.claude/`                       |
| `test`     | tests                                                     |
| `build`    | build, dependencies                                       |
| `ci`       | pipelines                                                 |
| `chore`    | anything else that does not belong in the product history |
| `revert`   | reverting a commit                                        |

### Scopes

| Scope       | Area                                                          |
| ----------- | ------------------------------------------------------------- |
| `bot`       | Telegram handlers, keyboards, message formatting              |
| `llm`       | LLM interface and provider clients                            |
| `research`  | research sources and snippet collection                       |
| `facts`     | fact extraction, quote verification, fact status              |
| `generator` | post and thread writing, number and date verification         |
| `style`     | deterministic style filter, critic pass, few-shot             |
| `storage`   | persistence (SQLite, later)                                   |
| `images`    | images for posts (Wikimedia Commons, later)                   |
| `deps`      | dependencies                                                  |
| `ci`        | pipelines                                                     |
| `docs`      | documentation under `.claude/` and the top-level markdown     |
| `project`   | repository-wide setup that fits no other scope                |

The scope is optional when a change is not tied to one place.

### Breaking changes

A `!` after the scope plus a footer:

```
refactor(llm)!: rename complete_json to complete_structured

BREAKING CHANGE: every pipeline step that calls the client must be updated in the same commit.
```

## Jira (project HIS)

The key is `HIS-<number>`, always uppercase: `HIS-1`, not `his-1` or `His1`. Jira does not
recognise a lowercase key and the link silently fails.

Three places must carry it, each giving a different link:

| Where                                 | Why                                                       |
| ------------------------------------- | --------------------------------------------------------- |
| Branch name                           | Jira shows the branch on the task and offers to open a PR |
| Commit footer                         | commits appear in the task's development panel            |
| PR title, at the end, in parentheses  | the PR attaches to the task and moves its status          |

```
docs(project): add project documentation (HIS-1)
```

Move the status yourself:

| When                                   | Status                                      |
| -------------------------------------- | ------------------------------------------- |
| Picked it up, opened a branch          | **In Progress**                             |
| Opened a pull request                  | **In Review**                               |
| PR merged into `main`                  | **Done**                                    |
| Waiting on a reply, another PR, access | **Blocked**, with a comment saying what for |

> Status names are the defaults. If the board columns are named differently, adjust the table to
> the real workflow. The `HIS-` key does not change.

**One ticket, one branch, one PR.** If a ticket grows, split it into sub-tasks in Jira rather
than dragging unrelated changes into one branch.

**Findings outside the task** are not fixed in the same branch. Describe them (in the PR or in
the report after the work) and open a separate ticket. A mixed diff cannot be reviewed.

## What must ship in one commit

Some files drift apart silently. Change them together:

- **A style rule** (add, remove, reword) -> [`.claude/docs/style-rules.md`](.claude/docs/style-rules.md),
  the deterministic check or the critic prompt that enforces it, and the test for it.
- **A pipeline step, a data model field or a verifier** -> [`.claude/docs/pipeline.md`](.claude/docs/pipeline.md)
  and the code in the same change.
- **A layer, an import rule or a client interface** -> [`.claude/docs/architecture.md`](.claude/docs/architecture.md).
- **A decision on an open question** (trusted domains, search provider, images, length limits)
  -> recorded in [`.claude/docs/decisions.md`](.claude/docs/decisions.md), not just in chat.
- **A new setting** -> the Pydantic settings model and the example env file together.

## Pull requests

Run the checks locally before pushing:

```bash
make check    # ruff format check, ruff lint, mypy strict
make test     # pytest
make format   # ruff format and ruff check --fix, to repair what make check reports
```

If a check was not run, say so in the PR and give the reason.

The PR title is the commit subject plus the Jira key at the end:
`feat(facts): drop a fact whose quote is missing from the snippet (HIS-14)`.

The PR body is detailed and has three sections:

```markdown
## Summary
What changes and why. Decisions taken, and anything deliberately left out.

## Verified
What was actually checked and how: scenarios, inputs, observed results.
What was not verified, stated plainly.

## Checks
make check: pass / fail / not run
make test: pass / fail / not run

Closes https://astrotall.atlassian.net/browse/HIS-<n>
```

The `(HIS-<n>)` in the title is for Jira. The full URL in the body is for anyone, or any tool,
that pulls task context.

One PR, one ticket. Refactoring nearby code "while you are here" goes in a separate ticket.

## Checks and CI

`make check` and `make test` are the gate. The CI workflow `.github/workflows/ci.yml` runs them
on every pull request as two jobs, `check` and `test`, with Python 3.12 through `uv sync --locked`.
A red CI is never merged. A change to dependencies in `pyproject.toml` ships together with the
updated `uv.lock`.

Who runs git: the author. Claude working in this repository does not commit, push, create
branches or open pull requests (see [CLAUDE.md](CLAUDE.md) -> "Hard rules").
