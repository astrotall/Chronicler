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
| Snippet id is the hash of the normalised URL | Stable between runs: Tavily returns other chunks of the same page for other queries, so a hash of the text would change. Dedup by URL leaves one snippet per URL, so ids do not collide |
| Snippet `lang` is `None` for Tavily       | The Tavily API does not report a language. Guessing it would put wrong labels on snippets      |
| One Wikipedia article is one snippet      | Dedup by URL drops the fragment, so section snippets of one article would collapse into one     |
| Wikipedia text: lead plus following sections, up to a limit | See "Wikipedia extracts" below                                              |
| Wikipedia needs a contact in config, Tavily needs a key | Wikimedia's User-Agent policy asks for contact data. Without either, that source is off with a warning and the others work |
| Tavily `search_depth=basic` by default    | 1 credit per query instead of 2. `advanced` is a config value                                  |
| Every query goes to every source         | See "Source routing" below                                                                      |
| A duplicate query is an invalid plan, not a repair | The client's retry asks the model for a corrected list. Code silently dropping entries could leave fewer than 3 |
| The domain filter is in the orchestrator, for all origins | One place, one behaviour. Allow and block lists are empty by default              |
| Wikipedia language editions are one domain | Translations often copy each other's mistakes, so ru and en Wikipedia do not make a fact `confirmed`. Mirrors (wikimedia.org, ruwiki.ru, wikiwand.com) join them by default |
| The model groups one claim across snippets, code counts domains | Recognising "the same claim" needs language understanding; counting domains does not |
| Domain outside groups is the last two host labels | No public suffix list without a new dependency. Merging `x.co.uk` and `y.co.uk` errs towards `single`; the full host would err towards a false `confirmed` |
| Snippets are shown to the model as `S1`, `S2`... | A 16-character hash is easy to garble. An unknown label drops the support item, nothing is guessed |
| Fact text in Russian, quote in the snippet's language | The post is Russian; a translated quote could not be checked. The translation itself is guarded by the number check |
| Numbers of a fact must occur in its quotes | Step 4 checks the post's numbers against the fact text, so the fact text must be bound to the source too |
| Quote check normalises typography, not wording | See "Quote normalisation" below |
| One extraction call within a character budget, no batches | Grouping one claim across snippets needs all snippets in one call. The budget cuts long snippets to a common cap, see pipeline.md |
| Contradictions are a second pass on the `fact_extraction` step | Same kind of reading, same provider. It sees only fact texts, before the limit |
| A contradiction needs an explicit verdict from the model | Without it DeepSeek reported sequences of events as contradictions, even while writing "this is not a contradiction" |
| "Not enough facts" is a typed result, not an exception | `FactsExtracted \| InsufficientFacts` makes the writing step handle both, and the author still sees what was found |
| Extra candidates are dropped, not rejected | The model sometimes returns more than the 40 asked for. A retry repeats a whole extraction; dropping the tail loses nothing that was verified |
| Git hooks: `make check` and `make test` both in pre-commit, no pre-push | See "Git hooks" below                                                    |
| The topic is a frame for the writer, not a source | It tells the model what the post is about and what could hook. Every claim, number and date comes from the facts; a number only in the topic is reported as unverified |
| The writer sees fact ids, texts and statuses, never quotes or URLs | The post is written in the model's words, not copied from a source |
| Disputed facts go to the writer in their own block | A disputed fact is never in the list of facts to state, so the model cannot state it flatly by mistake |
| An unverified number in a post is a warning, not a regeneration | The author sees it next to the post. See "Matching numbers and dates" below |
| One writing method for every button      | Angle and revision are optional inputs of `write_draft`; a button re-enters step 4 without new code paths |
| The writer replies in JSON, fact ids first | `complete_json` validates the reply. Ids first make the model pick facts before it writes; the text is the last field |
| Length: one retry with the exact problems, then deliver marked | See "Length limits" below |
| Style rule data in `app/config/style.py`, one source | The writing prompt renders its rules from it and the style filter reads it. Not an env variable: a phrase contains a comma |
| Writing prompt: English structure, Russian style rules | Matches the other prompts. The rules block is in Russian with the phrases it bans. A fully Russian prompt is to be tried if the voice reads wooden |

## Details of decided questions

### Wikipedia extracts

Source: `list=search` for the top `WIKIPEDIA_MAX_ARTICLES` (2) titles, then one `prop=extracts`
request per article with `explaintext` and `exsectionformat=wiki`, so the headings stay as
`== Heading ==`. A full-article extract is limited to one page per request (the API lowers
`exlimit` to 1), so articles are fetched one after another. The same request asks for
`pageprops` and `info`: a page marked `disambiguation` is skipped, and the canonical URL is
taken from `fullurl`, with no extra request.

- Why not the lead only: the lead of "Куликовская битва" is 642 characters in a 44,000-character
  article. It has the date and the sides but little else, and the facts step would starve.
- What is kept: the lead whole, then the sections in article order, cut at a line boundary at
  `WIKIPEDIA_EXTRACT_MAX_CHARS` (6000). A heading left at the end of the cut is dropped.
- What is removed: everything from the first service section on (Примечания, Литература, Ссылки,
  См. также, Источники, Комментарии; References, External links, Further reading, See also,
  Notes, Bibliography, Sources, Footnotes, Citations). These sections are last in an article and
  hold book titles and years that would read as facts. The names are in
  `app/config/constants.py`.
- Stress marks: the Russian plain text contains the combining acute accent (`Кулико́вская`). It is
  removed. The quote check in the facts step compares against this text, and a model will not
  reproduce a mark that is invisible.
- Known limit: the cut keeps the start of the article. For a long article the 6000 characters end
  before the middle sections, so the later part (for the battle, the battle itself) is not in the
  snippet. Wikipedia gives context and dates, and the web search is expected to supply detail. If
  that proves too thin, the options are a larger limit or choosing sections per query.

### Tavily parameters

`POST https://api.tavily.com/search` with `Authorization: Bearer <key>`.

- `search_depth=basic`, `max_results=5`, `chunks_per_source=3`, all in config.
- `chunks_per_source` is sent for `basic`, `advanced` and `fast`, where the documentation says it
  applies, and not for `ultra-fast`.
- The snippet text is the `content` field: up to three short chunks of the page that match the
  query, joined with ` [...] `. It is cleaned text, not HTML. `raw_content` is not requested: a
  whole page is long, and its start is mostly navigation.
- Cost: 1 credit per query on `basic`, so up to 5 credits per topic. `advanced` is 2 credits.
- The response `score` is not used.

### Source routing

Every query goes to every source, with no routing by language. With 5 queries, two Wikipedia
editions and Tavily that is 15 `search` calls and about 35 HTTP requests per topic (each Wikipedia
call is 1 search plus up to 2 article requests). It is simple and wasteful: a Russian query sent
to English Wikipedia mostly finds little. Routing Russian queries to ru and English ones to en
can be added later in the orchestrator without touching the sources.

### Quote normalisation

Goal: catch an invented quote, never drop an honest one over typography. Removed or folded: stress
marks (a model does not reproduce an invisible mark), quote marks of every kind (models swap `«»`
for `""` or drop them), dashes and the spaces around them, ellipses as chunk boundaries, case,
`ё`/`е`, whitespace, soft hyphens and zero-width characters, end punctuation of a part. Not
folded: words, word order, digits, letters. A quote that differs from the source by one word or
one digit is not found. A part shorter than 20 characters never counts, because `в 1380 году`
occurs everywhere. The full order is in [pipeline.md](pipeline.md), step 3.

Measured on the Wikipedia fixtures with DeepSeek: across 6 runs, no support item was dropped.
DeepSeek copies quotes verbatim; normalisation was needed once (`""` for `«»`).

### Git hooks

Plain git hooks in `.githooks/`, enabled by `make setup-hooks` (`core.hooksPath`), with no hook
framework and no new dependency. `.githooks/pre-commit` runs `make check` and then `make test`.

- Timing, measured on the author's machine (HIS-20): `make test` took 3.2 to 4.1 s wall time over
  three runs (326 tests, pytest itself 2.6 to 3.1 s), `make check` 0.7 s. The threshold was about
  20 s, so `make test` goes in pre-commit and there is no pre-push hook.
- Revisit: if `make test` grows past about 20 s, move it to a `pre-push` hook and keep `make check`
  in pre-commit.
- The hooks only read. No auto-format, no staging, no network, no `integration` tests (those are
  excluded from `make test` by `addopts` in `pyproject.toml`).
- `--no-verify` is allowed. CI runs the same gate, so skipping only delays the failure.

## Open questions

Each question has a provisional default so work can proceed. The default is not a decision until
it is recorded in the "Decided" table.

### Trusted domains

Which domains count as reliable, which as weak, and whether a weak domain can contribute to
`confirmed` at all.

- Provisional default: all domains are equal, `confirmed` needs 2 distinct domains after the
  domain rules in [pipeline.md](pipeline.md), step 3.
- Consequence of leaving it: two content-farm pages that copy each other count as independent.
- To settle: a config list of trusted and blocked domains, and whether blocked domains are
  dropped before extraction or only excluded from status counting.

### Web search provider

Tavily is the starting choice. Open: whether its result quality, price and snippet length are
adequate for history topics, and whether a second provider is needed.

- The research source interface keeps this swappable.
- To settle: compare real runs on a handful of topics. Output of local comparisons goes to
  `data/comparisons/`, which is git-ignored.

### Matching numbers and dates

How the verifier compares a number in the post with the facts.

- Decided for step 4 (HIS-6): `extract_numbers` from `app/services/quote_check.py`, the same rules
  as in step 3 (separators, ranges, decimals; see [pipeline.md](pipeline.md)). The numbers of every
  part of the post must be among the numbers of the texts of all facts in the `FactSet`, disputed
  ones included. A mismatch goes to `Draft.unverified_numbers` as a warning; the post is not
  blocked and not regenerated. The numbering prefix of a thread is not checked.
- Known limits, accepted for now:
  - Roman-numeral centuries (`XIX век`) are not extracted, so they are neither matched nor
    reported.
  - Numbers in words (`двенадцать`) are not extracted either: a post that writes a number in
    words passes the check unseen.
  - Approximate wording is ignored: `около 300` and `300` are the same number to the check.
  - `1.500` reads as 1500; a date with dots splits into the day and month and the year.
- Still open: whether Roman numerals and numbers in words get a conversion, and whether step 5
  turns a warning into a regeneration.

### Merged claims

The model groups support for one claim during extraction (decided above). Open: the model may
merge two different claims into one fact, and two domains behind a merged fact would make it a
false `confirmed`. A cheap embedding or string-overlap check could back the grouping up. Not
decided.

### Images

Pictures for posts are planned through Wikimedia Commons, **later**. Open: how an image is picked,
how its licence and attribution are shown to the author, and whether the bot suggests or attaches
it. The `images` scope is reserved. No code until a ticket for it exists.

### Length limits

Decided as config values (HIS-6), defaults to be revisited after real drafts:

- Short post: `SHORT_MAX_CHARS`, 280.
- Long post: `LONG_MAX_CHARS`, 25000 (X Premium).
- Thread: `THREAD_TWEET_MAX_CHARS`, 280 per tweet, and `THREAD_MAX_TWEETS`, 12. The model splits
  at meaning boundaries; a tweet is never cut mid-sentence by code.
- Counting is `len()` of the part as delivered. X counts a URL as 23 characters and emoji and CJK
  as 2, after NFC; for Russian text without links and emoji the counts match.
- On a breach the model gets one retry with the exact parts and numbers. If it still breaks a
  limit, the draft is delivered with `length_violations`; code never truncates.
- Numbering (`1/ `) is off by default (`THREAD_NUMBERING`). Code adds it after the reply, the
  model is given a tweet limit reduced by the widest prefix, and the full length is checked.
- Open: how many facts a thread needs (see [pipeline.md](pipeline.md), failure behaviour).

### Few-shot selection

How examples enter the prompt once they exist: all of them, the N most recent, or the N most
similar to the topic. Also how many fit before the prompt costs more than the style gain.

- Provisional default, implemented in HIS-6: the first `EXAMPLES_MAX` (3) non-empty `*.md` files
  in `EXAMPLES_DIR` (`data/examples`) sorted by name. Hidden files, `.gitkeep`, other extensions
  and files that are not UTF-8 are skipped. No examples means no examples block.
- Open: an example has no length cap, so a long reference post makes every prompt longer.

### Critic strictness

What happens when the critic keeps finding violations after the 2 allowed regenerations.

- Provisional default (in [pipeline.md](pipeline.md)): deliver the draft with the unresolved
  violations listed.
- To settle: whether the author prefers a hard stop.

### Persistence content

What SQLite will hold when it arrives: only the fact and draft history, or also research
snippets and caching of repeated topics. Not needed until the no-database stage proves a gap.
