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

Input: the `FactSet`, the format (`short`, `long`, `thread`), the few-shot examples, and two
optional inputs for the buttons of step 6: an angle and a revision (an instruction with the
previous text). Output: a `Draft`. The caller passes a `FactSet` only after it has matched
`FactsExtracted`; deciding that `InsufficientFacts` is not enough for a post is not this step's
job. An empty `FactSet` is a `ValueError` before any call.

- LLM step: `writing`, one `complete_json` call. The prompt contains the facts and nothing else
  about the world.
- **The topic is a frame, not a source.** `FactSet.topic` is in the prompt to say what the post is
  about and what could hook the reader. Every claim, number and date comes from the facts block.
  A number taken from the topic is not among the facts and is reported like any other.
- **What the model sees of a fact:** its id (`F1`...), its text and its status. Never the quotes
  or the URLs: the post is written in the model's own words, not copied from a source.
- **A short post gets a selection of the facts.** Before the call, code picks at most
  `SHORT_MAX_FACTS` (3) facts for `short` (`select_short_facts`, `app/services/short_post.py`);
  `long` and `thread` get the whole `FactSet`. The rules:
  1. Facts that are not disputed, `confirmed` first, then `single`, in `FactSet` order inside each
     status. Step 3 already keeps the extraction model's order, which tends to open with the core
     facts. The length of a fact's text is not used: short facts are mostly side details.
  2. A dispute enters only whole and only if it fits into the slots left. Overlapping disputes
     merge into one unit; disputed facts with no group form one unit. With the default limit and 3
     or more facts to state, a short post gets no dispute.
  3. If no fact to state was picked and no unit fits, the first unit is taken whole, over the
     limit: a dispute is never given half.
  The prompt gets only the selection; the `FactSet` itself does not change, so the author still
  sees every fact and every dispute. The number check still uses the whole `FactSet`; the
  disputed part of `used_fact_ids` uses only the selection (see "Used facts" below). A
  consequence: every angle ("другой заход") of a short post gets the same facts.
- **Disputed facts are a separate block.** A fact is disputed if its status is `disputed` or it
  belongs to a `Dispute`. Such facts are shown only under the disputed header, grouped by
  dispute with its explanation, and never in the block of facts to state. The prompt asks for
  cautious wording ("по одним данным ..., по другим ...", "источники расходятся") or leaving the
  fact out.
- **Style rules** are rendered into the prompt from the same data the style filter of step 5
  uses (`app/config/style.py`, see [style-rules.md](style-rules.md)).
- **Examples** are a separate system message, marked as a sample of rhythm and manner whose
  wording, topics and facts must not be copied. With no examples the message is omitted.
- **Reply.** `{fact_ids, text}` for `short` and `long`, `{fact_ids, tweets}` for `thread` (2 or
  more tweets). `fact_ids` comes first, so the model picks its facts before it writes, and the
  text is the last field. Ids are matched like snippet labels (trimmed, square brackets dropped,
  case ignored); unknown ids are dropped and counted, repeated ones are kept once. The prompt asks
  the model to list the disputed facts it mentions as well.
- **Used facts (`used_fact_ids`).** The ids the model reported, in its order, then the disputed
  facts that code finds, in `FactSet` order (`app/services/disputes.py`). The bot marks disputed
  facts by this list, so a dispute stated in the text must not depend on the model reporting it.
  1. Candidates are the disputed facts of the set that went into the prompt: the selection for
     `short`, the whole `FactSet` for `long` and `thread`. A disputed fact is one with status
     `disputed` or in a `Dispute`.
  2. A candidate is found if it has numbers (`extract_numbers`) and all of them are among the
     numbers of the `text` of all parts together. Numbering prefixes are not part of `text`. A
     candidate with no numbers is never found, only reported by the model.
  3. Every `Dispute` group that has a reported or found fact is completed with the rest of its
     members that went into the prompt: a dispute is never marked by half. Overlapping groups
     merge, as in the short selection. A disputed fact outside any group is not completed.
  4. The text checked is the final one: after a retry or a tail drop, not the text of the first
     attempt.
- **Length.** Code counts `len()` of each part as delivered, numbering included. Limits:
  `SHORT_MAX_CHARS` (280), `LONG_MAX_CHARS` (25000), `THREAD_TWEET_MAX_CHARS` (280) per tweet and
  `THREAD_MAX_TWEETS` (12). If a part of a `long` post or a thread is too long or a thread has too
  many tweets, the model gets its reply back with a list of exactly which parts break which limit,
  once (`WRITING_LENGTH_RETRIES`). If the second reply still breaks a limit, the `Draft` is
  returned with `length_violations`. Code never cuts a `long` post or a thread.
- **Short post budget.** The format rule gives a budget derived from the limit instead of "aim
  below it": at most `SHORT_MAX_CHARS // SHORT_SENTENCE_CHARS` sentences (3 by default), each at
  most `SHORT_SENTENCE_CHARS` (80) characters, and the total. A model counts sentences better than
  characters.
- **Short post retry.** An overlong short post gets up to `SHORT_LENGTH_RETRIES` (2) retries. The
  correction states the length, the limit and the excess, and quotes the sentences to delete or
  shorten: the longest ones, until their total covers the excess. After the last retry the draft
  is returned with `length_violations`, as for the other formats.
- **Tail drop, off by default** (`SHORT_DROP_TAIL=false`). When on, a short post still over the
  limit after every retry loses its tail, by code: the longest run of whole leading paragraphs that
  fits is kept; if even the first paragraph is over, its trailing sentences go; if even the first
  sentence is over, nothing is dropped. The dropped pieces are listed in `Draft.dropped_tail` and
  `length_violations` is recomputed on the kept text. It is never applied when the post contains a
  number of any disputed fact (`extract_numbers`), because the drop could leave one version of a
  dispute without the other; the draft then stays whole with `length_violations`.
  The ids the model reported stay as reported for the full text, so after a drop they may name
  facts that are no longer in the post; the bot has to say so next to a non-empty `dropped_tail`.
  The disputed facts that code adds are computed on the kept text.
- **Sentence boundaries** (for the retry and the drop): `.`, `!`, `?` or `…`, optional closing
  quotes, whitespace, then an uppercase letter, a digit or an opening quote. A point after a
  one-letter word (initials, `г.`) or after a word in `SENTENCE_ABBREVIATIONS` is not a boundary.
  The rule errs towards joining two sentences, never towards cutting one; a paragraph boundary is
  a blank line.
- **Thread numbering** (`THREAD_NUMBERING`, off by default) is added by code after the reply, as
  a `prefix` of each part (`1/ `). The model is told a tweet limit reduced by the widest prefix;
  code checks the full length. The prefix is not part of `text`, so the number check and the
  style filter never see it.
- **Code checks numbers and dates.** Every number in every part (`extract_numbers`, the rules of
  step 3) must be among the numbers of the texts of all facts in the `FactSet`, disputed ones
  included, because a post may state both versions. Numbers that are not are listed in
  `Draft.unverified_numbers`. Step 4 itself does not reject or regenerate; step 5 turns every
  such number into a violation that triggers a regeneration (see step 5).
- An LLM error or an invalid reply is not caught. One INFO line logs the format and counters:
  parts, facts offered to the model, used facts, unknown ids, unverified numbers, length
  violations, attempts, dropped pieces. Texts are never logged.

Known limits of the number check:

- Roman numerals (`XIV век`) and numbers written in words (`двенадцать`) are not numbers for
  `extract_numbers`. A post that writes them is not checked for them. Style rule 15 asks the
  writer for digits in dates, years, terms, sums, sizes, ages and percentages and forbids computed
  intervals; small counts in words are allowed and stay unchecked.
- Approximation is not understood: `около 300` matches a fact with `300`, and a fact with `около
  300` matches a post that states exactly `300`.
- The limits of step 3 apply: `1.500` reads as 1500, a date with dots splits into `8.09` and the
  year.

Known limits of the length count: `len()` counts code points. X counts every URL as 23
characters, emoji and CJK characters as 2, and normalises to NFC first. For Russian text without
links and emoji the two counts match; posts here have no emoji by rule 9.

### 5. Style filter

Input: a `Draft`, its `FactSet`, the writing context (limits, examples, angle) and
`allow_closing_question`. Output: a `StyleResult`. The filter never edits the text itself: it
finds violations, asks the writer for a new version, and picks which version goes out.

`review_style(writer, critic, draft, fact_set, writing_limits, style_limits, examples, *, angle,
allow_closing_question)` in `app/services/style_review.py`.

#### Deterministic checks

`check_draft(draft, *, allow_closing_question)` in `app/services/style_filter.py`. It runs on
`Draft.texts`, so a numbering prefix is never checked. Every rule datum is read from
`app/config/style.py` at call time; adding a phrase is a change to that module only.

| Rule                  | Violation                                                                            |
| --------------------- | ------------------------------------------------------------------------------------ |
| `dash`                | Each character of `FORBIDDEN_DASHES` (em and en dash). A hyphen, spaced or not, never |
| `banned_phrase`       | A match of a phrase of `BANNED_PHRASES` (see "Phrase matching")                       |
| `invented_experience` | A match of a phrase of `INVENTED_EXPERIENCE_PHRASES`                                  |
| `emoji`               | A run of characters in `EMOJI_RANGES`. `№`, `°`, `©` are not emoji                     |
| `hashtag`             | `#` followed by a word, not after a letter or `&` (`C#`, `&#123;` pass)               |
| `closing_question`    | The last part ends with `?`, unless the caller allows it. A text that ends with a closing quote (`«Где войско?»`) is quoted speech and passes |
| `length`              | Each entry of `Draft.length_violations`, as computed by step 4. Never recounted        |
| `unverified_number`   | Each number of `Draft.unverified_numbers`                                              |

Each violation carries the part (1-based) and an excerpt from the original text: the match, or for
a dash and an emoji the character with up to `STYLE_EXCERPT_CONTEXT_WORDS` (3) words on each
side. A length violation has no excerpt, a number violation has the number. The explanation is a
Russian template from `app/prompts/style_critique.py`.

There is no deterministic triplet check: a list of names from the facts ("Армстронг, Олдрин и
Коллинз") is legitimate and would be a false positive. Rhetorical triplets are the critic's.

#### Phrase matching

No dependency. Each phrase becomes a regular expression:

1. The phrase is split into words. `X` and `Y` (`PHRASE_PLACEHOLDERS`) stand for 1 to
   `PHRASE_PLACEHOLDER_MAX_WORDS` (8) words.
2. A word shorter than `PHRASE_MIN_STEM_CHARS` (3) or listed in `BANNED_PHRASE_EXACT_WORDS` must
   match whole. Any other word loses its longest ending from `PHRASE_STEM_ENDINGS` that leaves at
   least 3 letters, and matches the stem followed by any letters: "давайте разберёмся" catches
   "Давай разберём", "стоит отметить" catches "стоило отметить".
3. Between words: if the phrase has a punctuation mark there (the comma of "X, а Y"), that mark
   with optional spaces; otherwise any run of characters that are neither letters nor sentence ends
   (`.!?…`). So a phrase never spans two sentences, and "не просто так" without ", а ..." passes.
4. Case is ignored and `е` matches `ё`. The match runs on the original text, so the excerpt is
   what the author wrote. Overlapping matches of one rule count once ("это не просто X, а Y" and
   "не просто X, а Y").

`BANNED_PHRASE_EXACT_WORDS` holds "заключение": by stem, "в заключение" would also catch "в
заключении мира" and "провёл 10 лет в заключении".

#### Critic

`critique_draft(client, texts, fact_set, max_findings)` in `app/services/style_critic.py`, LLM step
`style_critique`, one `complete_json` call (`STYLE_CRITIC_MAX_TOKENS`). Off with
`STYLE_CRITIC_ENABLED=false`.

- **What it sees:** the parts (numbered `[1]`... for a thread), the rules block
  (`render_style_rules`, the same text the writer gets), the facts that may be stated as `F1
  [status]: text`, and the disputed facts grouped by dispute with its explanation, as the writer
  sees them. The whole `FactSet`, not the short selection: a claim is supported if any fact states
  it. Never quotes or URLs.
- **What it looks for:** `unsupported_claim` (a conclusion, cause, consequence or claim of
  importance no fact states; an added qualifier such as "по преданию"; an added precision or
  emphasis such as "точно", "уже"; a computed interval; a disputed fact stated as established),
  `ambiguous_reference` (a pronoun or omitted subject that makes the sentence claim something the
  facts do not), `filler`, `opinion` (over `OPINION_MAX_PER_POST` or as a closing line), `cliche`,
  `triplet` (rhetorical only; a list from the facts is not one) and `invented_experience`.
- **Not its job:** honest retelling of a fact in other words (an explicit exception in the prompt),
  cautious wording of a dispute, punctuation, emoji, hashtags, the closing question, length, digits,
  and the hook, rhythm and thread structure (rules 7, 8, 16 are not enforced automatically).
- **Reply:** `findings`, each `excerpt`, `rule`, `explanation` (Russian, for the author, at most
  `STYLE_CRITIC_EXPLANATION_MAX_CHARS`), then `violation`. The verdict comes last so the model
  reasons first; a finding with `violation: false` is withdrawn and counted. The rule is limited to
  the critic's rules by the schema; an excerpt is at most `STYLE_CRITIC_EXCERPT_MAX_CHARS`.
- **Code checks every excerpt.** It must occur in a part after the quote normalisation of step 3
  (`check_quote`, case, spaces, quote marks, `ё`), at least `STYLE_CRITIC_EXCERPT_MIN_CHARS` (3)
  long. Code assigns the part. A finding whose excerpt is in no part is dropped and counted: the
  critic can invent too. A repeated finding (same rule, part and normalised excerpt) is kept once.
- At most `STYLE_CRITIC_MAX_FINDINGS` (10) findings are used, the first ones after withdrawal;
  the rest are dropped and counted, not rejected, because a retry repeats the whole review.

#### Regeneration loop

1. The draft is evaluated: deterministic checks, then the critic. The critic runs even when code
   found something, so one regeneration fixes everything at once.
2. If a violation other than `length` remains and the limit `STYLE_MAX_REGENERATIONS` (2) is not
   used up, `write_draft` is called again with the same `FactSet`, format, examples and angle, and a
   `Revision`: the previous texts and an instruction that lists every violation with its part,
   excerpt and explanation and says to fix only these and keep the rest. The excerpts of phrase
   rules found in this or any earlier attempt (banned phrase, invented experience and every critic
   rule except `ambiguous_reference`) are listed as forbidden, so one stock phrase is not swapped
   for another.
3. The new draft goes through all of step 4 again (short selection, length retries, number check)
   and then through step 5 from point 1.
4. **A length violation alone does not regenerate:** step 4 already spent its own retries on it.
   It is reported, counts when the best version is chosen, and is in the instruction when a
   regeneration happens for another reason.
5. With no violation there is no extra call: one critic call and no writer call.
6. **The best version goes out:** the fewest violations of the rules in `DANGEROUS_STYLE_RULES`
   (`unsupported_claim`, `unverified_number`, `ambiguous_reference`, `invented_experience`, data in
   `app/config/style.py`), then the fewest violations in total, and on a tie the later one. Its
   remaining violations are in `StyleResult.report`; nothing is hidden.

Unverified numbers regenerate since HIS-7: style rules say a figure not among the facts is
rejected, and a regeneration that names the number is cheap. A false positive of the number check
costs up to 2 extra writer calls.

#### Failures

- **The critic fails** (`LLMError`, including an invalid reply after the client's retries): the
  error class is logged at WARNING, that evaluation keeps its deterministic violations and gets
  `critic = failed`. The post is never lost. Code violations still regenerate; a version that is
  clean for code and unchecked by the critic counts as 0 violations, so the bot must show the
  "critic did not check this text" warning from `report.critic`.
- **A regeneration fails** (`LLMError` from `write_draft`): logged at WARNING, the loop stops,
  `regeneration_failed` is set, and the best of the versions already evaluated goes out with its
  violations.
- One INFO line per review: format, attempts, chosen attempt, regenerations, whether a
  regeneration failed, the critic status of each attempt, the number of violations, dangerous ones,
  counts per rule name, and the dropped, withdrawn and over-limit findings. Texts, excerpts and
  explanations are never logged.

Worst case per post: 3 critic calls and 2 `write_draft` calls, each of which may make up to 3 writer
calls for a short post (the length retries).

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

All of them are Pydantic v2 models and live in `app/domain/`.

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
| `PostFormat`        | `short`, `long`, `thread`                                                                                 |
| `DraftPart`         | One part of a post: `text` as the model wrote it and a `prefix` added by code (numbering); `rendered` is the two joined |
| `LengthViolation`   | `issue` (`part_too_long` or `too_many_parts`), the 1-based `part` or none, `actual` and `limit`            |
| `SentenceBudget`    | `max_sentences` and `sentence_chars` of a short post, derived from `SHORT_MAX_CHARS` and `SHORT_SENTENCE_CHARS` |
| `Revision`          | `instruction` and `previous` (the texts of the previous draft's parts), for "короче" and "ещё вариант"    |
| `Draft`             | `post_format`, `parts` (exactly one for `short` and `long`), `used_fact_ids`, `unverified_numbers`, `length_violations`, `attempts`, `dropped_tail` (pieces a tail drop removed, empty by default). `texts` gives the parts without numbering, the one input for the style filter; `rendered` gives what the author copies |
| `StyleRule`         | The rules a violation names: `dash`, `banned_phrase`, `invented_experience`, `emoji`, `hashtag`, `closing_question`, `length`, `unverified_number` (code) and `cliche`, `triplet`, `filler`, `opinion`, `unsupported_claim`, `ambiguous_reference` (critic; `invented_experience` too) |
| `Violation`         | `rule`, `source` (`code` or `critic`), `part` (1-based or none), `excerpt` (from the text, none for length), `explanation` in Russian for the author |
| `StyleReport`       | `violations`, `critic` (`checked`, `disabled`, `failed`), counters of critic findings dropped (excerpt not in the text), withdrawn and over the limit; `passed` is no violations |
| `StyleResult`       | The `draft` that goes out, its `report`, `attempts` (versions evaluated), `chosen_attempt`, `regenerations` and `regeneration_failed` |

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
| The critic's excerpt occurs in the post                 | Code       | Substring match after the quote normalisation            |
| Dashes, banned phrases, emoji, hashtags, closing question | Code     | Exact patterns and stems, data in `app/config/style.py`  |
| Cliches, rhetorical triplets, filler lines, opinions, ambiguous pronouns, unlisted invented experience | Model | Cannot be reduced to a pattern reliably |
| Hook, rhythm, thread structure                          | Nobody     | Prompt and few-shot only, not checked                    |
| Which version of a post goes out                        | Code       | Dangerous violations first, then the total               |
| Post length against the configured limit                | Code       | Plain count                                              |

A number the code cannot match to a fact is reported even if it happens to be correct. Step 5
regenerates the post once it sees such a number; if the number survives the regenerations, the
post still goes out with it listed, and the author decides.

## Failure behaviour

- A source is down: skip it, continue, tell the author which sources answered.
- Fewer than `FACTS_MIN_FACTS` facts that can be stated survive step 3: the step returns
  `InsufficientFacts`, the pipeline stops and says so. Do not write a post from the topic alone.
- Fewer facts than a thread needs: say so and offer a short post.
- The critic fails: the post goes out with the deterministic findings and `critic = failed`, and
  the author is told the critic did not check it. A failed regeneration keeps the best version
  already written. See step 5.
- The provider for a step is unavailable: the error names the step and the provider. There is no
  silent fallback to the other provider, because that would change the cost and the voice
  without the author knowing.
