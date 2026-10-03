# Decisions

What is decided, and what is still open. The locked decisions are the table in
[CLAUDE.md](../../CLAUDE.md). This file holds the reasons, and the questions that are not yet
closed. A decision on an open question is recorded here in the same change that acts on it.

## Decided

| Decision                                  | Reason                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------- |
| Personal bot, one user, ID whitelist      | The bot serves one author. Multi-user means accounts, quotas and isolation, none of it needed |
| No publishing to X                        | The author reviews and posts by hand. It removes a whole class of risk and an API dependency |
| Post only from facts with sources         | The main failure of an LLM here is an invented date, number or quote                         |
| Verbatim quote per fact, checked by code  | A model cannot certify its own quote. A substring check can                                  |
| Numbers and dates in the post checked by code | Catches the writing step inventing a figure the facts do not contain                      |
| Anthropic and DeepSeek behind one interface | Cost and voice differ per provider and may differ per step. Cheap to start, cheap to switch |
| Provider chosen per step, start with DeepSeek | Planning and extraction are mechanical, writing and critique are where voice matters      |
| No database first, SQLite later           | Early iterations do not need persistence. SQLite is a file and needs no service              |
| PostgreSQL, Redis, Arq, Alembic, Docker deferred | One user and one process do not need them. Each needs its own ticket with a stated reason |
| Fact status `confirmed` / `single` / `disputed` | The author sees how well a claim stands, and disputed claims are written cautiously    |
| Few-shot is the main style lever          | A banned-phrase list only moves the cliche. Examples set the voice                           |
| The mechanism works with zero examples    | There are no reference posts at the start                                                   |
| Tavily as the starting web search         | One API, built for LLM use, returns text snippets. Not a lock-in, see below                 |
| LLM clients on plain `httpx`, no SDK      | Two small endpoints. No new dependency, and retries, timeouts and errors stay under our control |
| Structured replies by schema instruction, validation and retry | Works the same on both providers. DeepSeek adds `json_object` mode. Anthropic's native `output_config.format` takes a subset of JSON Schema, and an unsupported keyword is a 400 with no retry |
| DeepSeek thinking off by default          | Thinking ignores `temperature` and spends `max_tokens` on reasoning. `DEEPSEEK_THINKING=true` turns it on |

## Open questions

Each question has a provisional default so work can proceed. The default is not a decision until
it is recorded in the "Decided" table.

### Trusted domains

Which domains count as reliable, which as weak, and whether a weak domain can contribute to
`confirmed` at all.

- Provisional default: all domains are equal, `confirmed` needs 2 distinct registrable domains.
- Consequence of leaving it: two content-farm pages that copy each other count as independent.
- To settle: a config list of trusted and blocked domains, and whether blocked domains are
  dropped before extraction or only excluded from status counting.

### Wikipedia language editions and independence

Whether `ru.wikipedia.org` and `en.wikipedia.org` are two independent domains.

- Provisional default: they are the same domain (`wikipedia.org`), so two Wikipedia articles do
  not make a fact `confirmed`. Translations often copy each other's mistakes.
- To settle: confirm, or count by language edition.

### Web search provider

Tavily is the starting choice. Open: whether its result quality, price and snippet length are
adequate for history topics, and whether a second provider is needed.

- The research source interface keeps this swappable.
- To settle: compare real runs on a handful of topics. Output of local comparisons goes to
  `data/comparisons/`, which is git-ignored.

### Matching numbers and dates

How the verifier compares a number in the post with the facts.

- Cases to define: "1 812" and "1812", "в 1812 году" and "1812 г.", "XIX век" and "19 век",
  "двенадцать" and "12", ranges ("1941-1945"), approximate wording ("около 300").
- Provisional default: extract digits and Roman-numeral centuries, normalise separators, compare
  as strings; numbers written in words are not matched and count as a violation until a
  conversion is added.
- A false alarm costs one regeneration, so the default errs strict.

### How the facts of one claim are grouped

Which facts from different sources are "the same claim", needed to count independent domains.

- Provisional default: the model groups them during fact extraction and code counts the domains.
- Risk: the model may merge two different claims. A cheap embedding or string-overlap check
  could back it up. Not decided.

### Images

Pictures for posts are planned through Wikimedia Commons, **later**. Open: how an image is picked,
how its licence and attribution are shown to the author, and whether the bot suggests or attaches
it. The `images` scope is reserved. No code until a ticket for it exists.

### Length limits

- Long post: configurable, 25000 characters by default (X Premium). Decided as a config value.
- Short post: no number yet. Needs a default.
- Thread: the per-post limit, the maximum number of posts, and whether a post may be cut
  mid-sentence (it must not).
- To settle: pick defaults after a few real drafts.

### Few-shot selection

How examples enter the prompt once they exist: all of them, the N most recent, or the N most
similar to the topic. Also how many fit before the prompt costs more than the style gain.

- Provisional default: the first N files in `data/examples/` sorted by name, N from config.

### Critic strictness

What happens when the critic keeps finding violations after the 2 allowed regenerations.

- Provisional default (in [pipeline.md](pipeline.md)): deliver the draft with the unresolved
  violations listed.
- To settle: whether the author prefers a hard stop.

### Persistence content

What SQLite will hold when it arrives: only the fact and draft history, or also research
snippets and caching of repeated topics. Not needed until the no-database stage proves a gap.
