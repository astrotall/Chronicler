# Pipeline

The bot turns a topic into a Russian post or thread in six steps. This document is the authority
on what each step does and, above all, on **what code verifies and what the model decides**.

The principle: a model is allowed to read, select and write. It is never trusted to certify its
own output. Every claim that matters is checked by deterministic code against a source text.

## Steps

### 1. Query planning

Input: the topic the author sent. Output: 3-5 search queries, in Russian and English.

- LLM step: `query_planning`.
- Response is validated by a Pydantic model; a malformed reply is a failure, not a guess.
- Code enforces the count (3-5), non-empty queries and no duplicates (compared ignoring case).
  A reply that breaks this is invalid: the client asks the model again, and after the retries
  the step raises `LLMInvalidResponseError`. Code does not repair the list by dropping entries.

### 2. Research

Input: the queries. Output: a list of `Snippet`.

- No LLM. Every research source implements `search(query) -> list[Snippet]` (see
  [architecture.md](architecture.md)). Sources: Wikipedia ru, Wikipedia en, Tavily.
- Sources run concurrently. A source that fails is logged and skipped; the pipeline continues
  with the others. The research step returns the snippets together with the list of failures
  and does not raise for a source error. If every source fails, the result has no snippets and
  the pipeline stops with an error to the author.
- Snippets are deduplicated by normalised URL; of several snippets with one URL the longest text
  stays. Their text is cut to a configured length. Domain allow and block lists are applied first.
  Duplicates by text across different URLs are not removed.

### 3. Fact extraction

Input: the topic and the snippets. Output: `FactsExtracted` (a `FactSet`) or `InsufficientFacts`.

- LLM step: `fact_extraction`. The model sees the snippets under short labels `S1`, `S2`... and
  returns atomic facts. Each fact has a statement in Russian and a support list; each support item
  is a snippet label and a **verbatim quote** from that snippet, in the snippet's language. When
  several snippets state the same claim, the model makes one fact with several support items.
  The topic is in the prompt only to say what is relevant, never as a source.
- **Code verifies every quote.** A support item is dropped when its label is unknown, its quote
  is too short, or the quote does not occur in the text of the named snippet after normalisation
  (see "Quote check" below). A fact left with no verified support is dropped whole. Only verified
  support is kept. The model is never asked "is this quote real".
- **Code verifies the numbers of a fact.** Every number in the text of a fact must occur among the
  numbers of its verified quotes (see "Number check"). Otherwise the fact is dropped: the
  translation into Russian is the one place the model could change a figure, and step 4 checks
  the post's numbers against the text of the facts.
- Code assigns `confirmed` or `single` from the domains behind the verified support.
- A second LLM pass on the same step looks for contradictions. It sees only the ids and texts of
  the verified facts, before the limit, and returns groups of contradicting facts with a short
  explanation and a verdict. Code turns the groups into `disputed`.
- Code sorts, applies the limit, assigns the ids `F1`, `F2`..., and decides whether there are
  enough facts.

#### Input budget

The snippet texts shown to the model are limited to `FACTS_INPUT_MAX_CHARS` in total (60000).
Extraction is one call, because the grouping of one claim across snippets happens inside the
call; batches would split it. If the snippets are longer than the budget, code finds the largest
common cap such that the snippets cut to it fit, and cuts every snippet longer than the cap at a
word boundary. Short snippets stay whole and every snippet stays in, so every domain still has a
chance to confirm a fact; long Wikipedia articles are cut first. Quotes are checked against the
text the model saw.

#### Quote check

Quote and snippet text are normalised the same way, in this order:

1. Unicode NFD, then the stress marks (U+0301, U+0300), soft hyphen, zero-width characters and all
   quote marks and apostrophes are removed. Then NFKC (no-break and thin spaces become spaces,
   `…` becomes `...`), case folding, and `ё` becomes `е`.
2. The text is split into segments at ellipses: `[...]`, `[…]`, `...`, `…`. Tavily joins the
   chunks of a page with ` [...] `, so a segment is one chunk.
3. In each segment every dash or hyphen (`-`, `‐`, `‑`, `‒`, `–`, `—`, `―`, `−`) with the spaces
   around it becomes `-`, runs of whitespace become one space, and `.,;:!?` and spaces are
   stripped from the ends. Empty segments are dropped.

The quote's segments (its parts, if the model put an ellipsis in it) must each be at least
`FACTS_MIN_QUOTE_CHARS` (20) characters long, otherwise the support is "too short". They must
occur in the snippet's segments in order, each part inside one segment. So a quote cannot span
two Tavily chunks, which are not adjacent on the page.

#### Number check

Numbers are digit runs in the NFKC text, with separators read as follows:

- A space (also no-break, thin and narrow no-break), comma, dot or apostrophe between digit
  groups, where the left part starts with 1-3 digits and the right group has exactly 3 digits, is
  a thousands separator and is removed: `150 000`, `150,000`, `150.000`, `150'000` are `150000`.
- A comma or dot followed by a group that is not 3 digits is a decimal separator: `3,5` and `3.5`
  are both `3.5`. Leading zeros of the integer part and trailing zeros of the fraction are dropped.
- Anything else ends a number, so `1320-1330` and `1320—1330` give `1320` and `1330`.

Every number of the fact text must be among the numbers of its verified quotes. A fact with no
numbers passes.

Known limits:

- `1.500` and `1,500` are read as 1500, never as 1.5.
- A date written with dots, `08.09.1380`, reads as `8.09` and `1380`, so a fact `8 сентября 1380`
  does not match it and is dropped.
- A number written in words in the quote does not support digits in the fact: the fact is
  dropped. Roman numerals (`XIV век`) are not numbers for this check and are not verified.
- `1380 300` (a year next to a count) splits into `1380` and `300` only because the left part has
  4 digits; `380 300` would read as one number.

#### Status

The domain of a support item is computed from its url (`app/services/source_domain.py`):

1. The host is lowercased, a trailing dot and a leading `www.` are removed.
2. If the host equals a member of a group in `FACTS_DOMAIN_GROUPS` or is its subdomain, the domain
   is the group's first member. The default is one group of Wikipedia and its mirrors:
   `wikipedia.org,wikimedia.org,ruwiki.ru,wikiwand.com`.
3. Otherwise the domain is the last two labels of the host: `news.example.com` is `example.com`.

Two or more different domains among the verified support make the fact `confirmed`, otherwise it
is `single`. Without a public suffix list the last two labels sometimes merge unrelated sites
(`bbc.co.uk` and `x.co.uk` are both `co.uk`). That errs towards `single`, never towards a false
`confirmed`.

#### Contradictions

- The pass gets the verified facts as `C1: text` lines, before the limit, so a disputed fact
  outside the limit is not lost. It is skipped when there are fewer than 2 facts.
- Each reported group has ids, an explanation in Russian for the author, and a verdict
  `contradiction`. The verdict comes after the explanation, so the model reasons first; a group
  with `contradiction: false` is withdrawn. Without it the model reported sequences of events
  ("given, taken away, returned") as contradictions.
- Code drops unknown ids and repeated ids. A group with fewer than 2 known ids is dropped.
- Every fact in a group is `disputed`, which overrides `confirmed` and `single`. The groups are
  kept in `FactSet.disputes` with the final ids.
- An LLM error or an invalid reply is not caught; it propagates as in every other step.

#### Limit, minimum and ids

- Facts that are not disputed are sorted `confirmed` first, then `single`, keeping the model's
  order inside each status. The first `FACTS_MAX_FACTS` (20) are kept.
- Disputed facts are always kept, on top of the limit, so the author sees them.
- The output is the kept facts, then the disputed ones; ids `F1`, `F2`... follow this order.
- If fewer than `FACTS_MIN_FACTS` (3) facts are kept that are not disputed, the result is
  `InsufficientFacts` with whatever survived. Disputed facts never count towards the minimum,
  because they cannot be stated. Code never fills the gap.
- At most 40 candidates from the model are used, the first ones. The model is asked for at most
  40 but sometimes returns more; the extra ones are dropped and counted, not rejected, because a
  retry of the whole reply costs a full extraction.
- No snippets means `InsufficientFacts` without a call to the model.

### 4. Writing

Input: the `FactSet`, the requested format (short, long, thread), the few-shot examples. Output:
a `Draft`.

- LLM step: `writing`. The prompt contains the facts and nothing else about the world. The raw
  topic is passed only as a framing hint, never as a source of claims.
- **Code verifies numbers and dates.** Every number and date in the draft must appear among the
  facts. A draft that contains a figure absent from the facts is rejected and regenerated,
  counted against the same attempt budget as step 5.
- `disputed` facts are written cautiously: attributed ("по версии ...", "источники расходятся"),
  never stated flatly.

### 5. Style filter

Input: a `Draft`. Output: an accepted `Draft`, or a regeneration request.

- Deterministic checks first (cheap, exact): dashes, banned phrases, emoji, hashtags, a closing
  question, structural tells. The full list is in [style-rules.md](style-rules.md).
- Then the LLM critic (`style_critique`) judges what regexes cannot: triplets, a flat opening, a
  uniform rhythm, invented personal experience, tone drift from the few-shot examples.
- On any violation the writer is called again with a list of exactly what to fix. At most 2
  regeneration attempts. If the draft still fails, it is delivered anyway with the unresolved
  violations listed, so the author decides. The bot does not silently loop.

### 6. Delivery

Input: the accepted `Draft` and its `FactSet`. Output: Telegram messages.

- The post comes first. Under it, the facts that were used, each with its source link and status.
- A fact with status `disputed` is marked with the word "СПОРНО" in the reply.
- Buttons:

| Button          | Intent                                                                         |
| --------------- | ------------------------------------------------------------------------------ |
| короче          | Rewrite the same draft shorter, from the same facts                            |
| в тред          | Rewrite the same facts as a thread                                             |
| другой заход    | A different angle on the topic; may select different facts from the same set   |
| ещё вариант     | The same angle, a different wording                                            |

Every button re-enters the pipeline at step 4. None of them re-runs research, so a click is
cheap and the facts the author already checked stay the same.

## Data models

All of them are Pydantic v2 models and live in `app/domain/`. `Draft` is still an idea; its
fields are fixed when the code is written.

| Model               | Fields and meaning                                                                                       |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| `Snippet`           | A piece of source text as returned by a research source: id, origin, title, URL, text, language          |
| `SourceRef`         | Evidence for a fact: `snippet_id`, `url`, `domain` (after the domain rules), the verified verbatim `quote` |
| `Fact`              | `id` (`F1`...), `text` in Russian, `support` (one or more verified `SourceRef`), `status`                 |
| `FactStatus`        | `confirmed`, `single`, `disputed`                                                                         |
| `Dispute`           | `fact_ids` (2 or more) and an `explanation` in Russian for the author                                     |
| `FactSet`           | `topic`, `facts`, `disputes`                                                                              |
| `ExtractionStats`   | Counters only: snippets, candidates, support proposed and dropped by reason, facts dropped by reason, disputes, facts cut by the limit |
| `FactsExtracted`    | `outcome = extracted`, the `FactSet` and the stats                                                         |
| `InsufficientFacts` | `outcome = insufficient_facts`, the `FactSet` of what survived, `assertable_count`, `required`, the stats |
| `FactExtraction`    | `FactsExtracted \| InsufficientFacts`. A caller has to tell them apart; step 4 accepts only `FactsExtracted` |
| `Draft`             | A post or thread: its parts, the format, the ids of the facts it uses, the attempt number, the violations found |

### Fact status

| Status      | Meaning                                                                                  |
| ----------- | ---------------------------------------------------------------------------------------- |
| `confirmed` | The same claim stands on 2 or more independent domains                                   |
| `single`    | One source, or several sources on the same domain                                        |
| `disputed`  | Sources contradict each other on this claim                                              |

`disputed` takes precedence over the other two. "Independent" means a different domain after
the domain rules in step 3: language editions of Wikipedia and its mirrors are one domain.

## Who checks what

| Check                                                   | Done by    | Why                                                      |
| ------------------------------------------------------- | ---------- | -------------------------------------------------------- |
| LLM reply is valid JSON of the expected shape           | Code       | Pydantic validation                                      |
| Number of search queries                                | Code       | Plain count                                              |
| Quote occurs in the snippet text                        | Code       | Substring match after normalisation, see step 3          |
| Numbers of a fact occur in its quotes                   | Code       | Number extraction and set comparison                     |
| Fact status (domain count)                              | Code       | Counting domains after host rules and groups             |
| Which facts say the same thing                          | Model      | Needs language understanding                             |
| Which facts contradict each other                       | Model      | Needs language understanding; code turns the mark into `disputed` |
| Numbers and dates in the post occur among the facts     | Code       | Extraction and set comparison                            |
| Every claim in the post is supported by a fact          | Model      | The critic reads the post against the facts              |
| Dashes, banned phrases, emoji, hashtags, closing question | Code     | Exact patterns                                           |
| Triplets, flat opening, uniform rhythm, invented experience | Model  | Cannot be reduced to a pattern reliably                  |
| Post length against the configured limit                | Code       | Plain count                                              |

A number the code cannot match to a fact is treated as a failure even if it happens to be
correct. A false alarm costs one regeneration; a missed invention costs the author's credibility.

## Failure behaviour

- A source is down: skip it, continue, tell the author which sources answered.
- Fewer than `FACTS_MIN_FACTS` facts that can be stated survive step 3: the step returns
  `InsufficientFacts`, the pipeline stops and says so. Do not write a post from the topic alone.
- Fewer facts than a thread needs: say so and offer a short post.
- The provider for a step is unavailable: the error names the step and the provider. There is no
  silent fallback to the other provider, because that would change the cost and the voice
  without the author knowing.
