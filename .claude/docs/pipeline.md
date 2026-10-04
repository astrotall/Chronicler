# Pipeline

The bot turns a topic into a Russian post or thread in six steps. This document is the authority
on what each step does and, above all, on **what code verifies and what the model decides**.

The principle: a model is allowed to read, select and write. It is never trusted to certify its
own output. Every claim that matters is checked by deterministic code against a source text.

## Steps

### 1. Query planning

Input: the topic the user sent. Output: 3-5 search queries, in Russian and English.

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
  the pipeline stops with an error to the user.
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
- **The stance of each fact** (HIS-32): `asserted`, `claimed` or `rebutted`, how the snippet itself
  presents the claim. The model gives it, code checks that the attribution is visible next to a
  quote (see "Stance" below). A `claimed` or `rebutted` fact is an attributed claim: never
  `confirmed`, never stated as fact.
- Code assigns `confirmed` or `single` from the independent, not weak, domains behind the verified
  support (see "Status" below), and marks every support item `weak` or not.
- A second LLM pass on the same step looks for contradictions. It sees only the ids and texts of
  the verified facts, before the limit, and returns groups of contradicting facts with a short
  explanation and a verdict. Code turns the groups into `disputed`.
- A third LLM pass on the same step ranks the facts that may be stated by how well they answer
  the topic (HIS-27, see "Relevance" below). Code validates the reply, sets aside the facts off the
  topic and orders the rest; the model never adds, rewrites or removes a fact.
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

Two or more different domains among the verified support that are **not weak** make the fact
`confirmed`, otherwise it is `single`. Without a public suffix list the last two labels sometimes merge unrelated sites
(`bbc.co.uk` and `x.co.uk` are both `co.uk`). That errs towards `single`, never towards a false
`confirmed`.

An attributed fact (stance `claimed` or `rebutted`) is always `single`, whatever its domains: two
sites repeating one myth do not confirm it.

#### Stance

Found in a manual long run on the Soviet daily life topic: «В СССР в 1930 и 1931 годах существовало
30 февраля» reached the post as a fact. The Wikipedia page says that many sources claim it and then
that the calendars of the two years coincide. The quote was verbatim, so the quote check passed; the
meaning was inverted. The contradiction pass did not help, because the rebuttal was not a fact.

- **Values.** `asserted`: the snippet states the claim as true, the default and by far the most
  common stance, also when the snippet cites a document or a count it relies on («по данным
  переписи», «согласно указу»). `claimed`: the snippet distances itself, as a legend, a tradition, a
  chronicle's story, a common belief, a myth, what many or some sources say, a version of some
  historians, «якобы». `rebutted`: the snippet gives the claim and then rebuts it.
- **Reply.** Each fact has `stance` (first, so the model decides before it writes), `text`,
  `support` and an optional `rebuttal`: `{text, support}`, the rebutting statement written as a
  plain fact. `stance` is written only for `claimed` and `rebutted`; an asserted fact leaves it out,
  which saves output tokens on the common case. The text of an attributed fact keeps the attribution («По преданию, ...»). When one
  snippet attributes a claim and another states it plainly, the prompt asks for one fact with the
  more cautious stance.
- **Parsing.** `stance` is a string in the schema and code reads it (trimmed, case ignored), so one
  odd value does not fail the whole reply. A missing stance is `asserted`, as before HIS-32. An
  unknown value (`legend`, `disputed`) drops the fact, counted in `facts_unknown_stance`: reading it
  as `asserted` is the bug this rule exists for, and reading it as `claimed` would trust an
  attribution nobody checked.
- **The rebuttal is a fact of its own.** It goes through the quote and number checks like any
  candidate and becomes an `asserted` fact. If it is verified, the claim is `rebutted` (also when the
  model wrote `claimed`) and `Fact.rebutted_by` of the claim lists the rebuttal's id. A rebuttal that
  fails its checks is dropped; the claim stays `rebutted` with an empty `rebutted_by`, and the writer
  is told to leave it out. A verified rebuttal whose claim failed is kept as a plain fact.
- **Code checks the attribution is visible** (`app/services/stance.py`). For each support item the
  context is the sentence of the snippet that holds the quote and `STANCE_CONTEXT_SENTENCES` (1)
  sentence on each side, inside one Tavily chunk (split at `[...]`) and one line. Sentences are
  `split_sentences` of step 4; the quote is located by `check_quote` on the shortest run of sentences
  that holds it, and if it is not found the context is the quote alone. A `claimed` fact needs one
  of `CLAIM_MARKERS` in the context of at least one support item; a `rebutted` fact one of
  `CLAIM_MARKERS` or `REBUTTAL_MARKERS` («однако», «на самом деле», «in fact»). Markers are matched by
  stem like the banned phrases (`phrase_pattern` of step 5), in Russian and English, data in
  `app/config/stance.py`. «однако» alone is too common to support `claimed`. The forms of «легенда»
  and «миф» (and `legend`, `myth`) match whole (`STANCE_MARKER_EXACT_WORDS`), so «легендарный»,
  «мифический» and «legendary» are not markers.
- **Strong and weak markers.** `CLAIM_MARKERS` is `STRONG_CLAIM_MARKERS` (legend, tradition, myth:
  «предание», «по преданию», «легенда», «сказание», «миф», «якобы», «будто бы», «по слухам», «legend
  has it», «allegedly», «supposedly», «it is said») and `WEAK_CLAIM_MARKERS` (a reported view or a
  source named: «считается», «утверждается», «по мнению», «по одной из версий», «многие
  источники», «указывается», «летопись», «хроника», «according to», «reportedly»). Weak markers
  only confirm a stance the model gave.
- **Code raises an asserted fact to `claimed`** (`facts_stance_upgraded_by_code`) when the context of
  at least one of its support items holds a strong marker. This covers a merge: one page attributes
  the duel of Peresvet and Chelubey to «Сказание о Мамаевом побоище», another states it plainly, and
  the model writes one asserted fact with both quotes. The cautious stance wins, the fact is `single`,
  and two sites never confirm a legend. A support item whose own sentence opens with a rebuttal
  opener (`REBUTTAL_OPENERS`: «однако», «на самом деле», «в действительности», «however», «in fact»,
  «actually») and holds no strong marker is not counted: it is the rebuttal of the legend next to
  it. The verified rebuttal of a rebutted fact goes through the same rule.
- **Code does not rewrite the text** of a raised fact. It may read as a plain statement («Сражение
  началось с поединка ...»); the attribution in the post comes from the attributed claims block of
  the writer and from the critic.
- **Swapped roles** (`facts_stance_role_swapped`). A `rebutted` fact whose quotes all sit in sentences
  that open with a rebuttal opener and hold no strong marker is the rebuttal itself, not the claim
  rebutted. It becomes `asserted` and is not linked to its "rebuttal"; that one, usually the real
  claim, then goes through the raise rule above. A sentence such as «Однако, по преданию, ...» keeps
  `rebutted`.
- **Without a marker the fact is lowered to `asserted`**, counted in `facts_stance_unmarked`. The
  check guards the other way round: the model must not mark a fact as a myth from its own knowledge
  or because it sounds doubtful. «По данным переписи 1939 года ...» marked `claimed` is lowered,
  because a census is not in the marker list. The cost: a real attribution worded outside the list
  is lowered too, so the list is broad and the live runs list every lowering.

Known limits:

- Only strong markers raise a fact. A plain fact next to a legend sentence is raised as well
  («Войска выстроились на поле. Предание рассказывает о поединке ...»), and a sentence that names
  the «Сказание» as a text (when it was written) raises the facts around it. Both err towards an
  attribution, never towards a myth stated as fact.
- A weak marker in a neighbouring sentence may belong to another claim («считается» about something
  else); the check then passes a wrong `claimed`. The same direction of error.
- An estimate with its attribution in the text («по оценкам историков, около 80 тысяч») is
  `asserted`: «по оценкам» is in neither list on purpose, so the guard does not pass every figure of
  historians as a version, and code does not raise it.
- A myth that another page states plainly as its own claim («Таким образом, ... существовало 30
  февраля») is `asserted` for that page. The model may mark it `claimed`, but with no marker in the
  page the guard lowers it; only the contradiction pass can catch it (HIS-32 round 2: it did not).
- Swapped roles are detected only by the opener of the sentence. A rebuttal that does not open with
  one («Судя по «Задонщине», Пересвет был жив») is not seen.

#### Weak sources

A weak source is a page whose host equals a domain of `FACTS_WEAK_DOMAINS` or is its subdomain
(`is_weak_source` in `app/services/source_domain.py`). The host is normalised like the domain above
(case, trailing dot, `www.`), but it is matched as a host and not as the last two labels, so
`otvet.mail.ru` can be weak while `news.mail.ru` is not. The default list holds video platforms,
blog hosting, social networks and Q&A sites, school presentation sites and AI slide generators;
the user edits it in `.env`, an empty value turns the rule off.

A weak source is not a blocked one. `RESEARCH_BLOCKED_DOMAINS` drops a page before extraction, so
the model never sees it. A weak page stays in the snippets and in `Fact.support`, the user sees
its link marked "слабый", but it is not an independent source:

- `SourceRef.weak` is set by code at verification (default `False`, so older snapshots load).
- A weak domain does not count towards `confirmed`. Two non-weak domains plus a weak one are still
  `confirmed`; one non-weak plus a weak one, or only weak ones, are `single`.
- `Fact.weak_only` is true when every support item is weak.

#### Contradictions

- The pass gets the verified facts as `C1: text` lines, before the limit, so a disputed fact
  outside the limit is not lost. It is skipped when there are fewer than 2 facts. A `claimed` fact
  is marked `C3 [claimed]: text`.
- **A claim and its denial are a contradiction** (HIS-32 round 3): one fact says an event, a date, a
  number or a thing was, another says it was not or is refuted. An attribution in the text («по
  утверждению источника», «по преданию», «по версии») does not remove the contradiction, and the
  `[claimed]` lines are compared with the plain facts and with the denials as well. The prompt gives
  two neutral examples (a bridge built in 1900 and not built; a tower by the gate in a legend and
  not found by the excavation). A version that only adds to a plain fact without denying it, a
  sequence of events, and a rounded number next to the exact one stay not contradictions. This is
  the only defence against a myth that a page states as its own conclusion with no marker: the
  stance guard lowers such a fact to `asserted`, and no code links facts by their numbers. A `rebutted` fact is left out: its relation to its rebuttal is already known, and
  the pair would come back as a contradiction that makes the true rebuttal "СПОРНО".
- Each reported group has ids, an explanation in Russian for the user, and a verdict
  `contradiction`. The verdict comes after the explanation, so the model reasons first; a group
  with `contradiction: false` is withdrawn. Without it the model reported sequences of events
  ("given, taken away, returned") as contradictions.
- Code drops unknown ids and repeated ids. A group with fewer than 2 known ids is dropped.
- Every fact in a group is `disputed`, which overrides `confirmed` and `single`. The groups are
  kept in `FactSet.disputes` with the final ids.
- An LLM error or an invalid reply is not caught; it propagates as in every other step.

#### Relevance

Found in live runs (HIS-27): the extraction model returns facts grouped by snippet, not by
relevance, so the limit kept the first ones and a short post on the Soviet daily life topic was
written from three facts about an exhibition. Most relevant facts were lost at the cut, not at the
search. The diagnosis is in [decisions.md](decisions.md), "Topic relevance".

- **Input.** The topic and `C1: text` lines of the facts that may be stated: verified, not
  disputed, not attributed. Never quotes, URLs or statuses. The topic is a frame, not a source.
  The pass runs after the contradiction pass and before the sort, only when
  `FACTS_RELEVANCE_ENABLED` is on (default) and there are at least 2 such facts.
- **Reply.** For each fact: `id`, `aspect` (1 to 3 Russian words naming the part of the topic),
  `about_source`, `outside_period`, and `relevance` 0 to 3 last (3 answers the topic, 2 useful
  context, 1 marginal or a repeat of a fuller fact, 0 not about the topic or no claim at all, such
  as a fact whose text is «Опровергнуто»).
- **`about_source` is narrow:** a fact about a page, an exhibition, a book, a publication, an author
  or a researcher (who they are, that the subject is topical, what the materials help to see). A
  fact about the name, the definition or the history of the subject, a fact that names who reports
  or estimates something about it (historians, a chronicle, a life of a saint) and a fact about how
  it is remembered are about the subject. The prompt gives neutral examples of both kinds.
- **Code validates.** Ids are matched like snippet labels; an unknown id, a repeated id (the first
  entry stays) and a relevance outside 0..3 drop the entry, counted in `relevance_dropped`. A fact
  with no valid entry counts as relevance 2 with the aspect `other`. Aspects are normalised (case,
  whitespace); the first `RELEVANCE_MAX_ASPECTS` (8) distinct ones in fact order keep their names,
  the rest become `other`, so a model that gives every fact its own aspect cannot defeat the turns
  below.
- **Code checks `outside_period`.** The model set it on facts dated inside the period («отменят уже в
  1935 году» for the 1930s), on direct causes and consequences (1929, 1940), and on a topic with no
  period at all. The flag stands only if the topic names years in digits (`1930-е`, `1930-х`,
  `1930s`, `в 1380 году`, `1914-1918`), the fact has at least one year (a number from 1000 to 2100),
  and every year of the fact is outside the topic's years widened by
  `RELEVANCE_PERIOD_MARGIN_YEARS` (10) on each side; a decade covers its ten years. Otherwise the flag
  is dropped and counted in `relevance_period_overruled`. A topic with a Roman-numeral century or no
  year never sets a fact aside as outside its period. Known limit: a count between 1000 and 2100
  («1200 рабочих») reads as a year; it can only confirm a flag the model already set.
- **Fallback.** An `LLMError` (an invalid reply after the client's retries included) or a reply with
  no valid entry keeps the model's order exactly, as before HIS-27: logged at WARNING with the error
  class, `relevance_failed` is 1. The extraction is never lost for the ranking.

#### Limit, minimum and ids

- **Off the topic is set aside.** A fact with `about_source`, a confirmed `outside_period` or
  relevance 0 does not enter the sort, the cap or the limit, whatever its status: a fact not about
  the subject is not wanted however well it is sourced. It comes back only for the floor below,
  after the facts the cap set aside, in priority order, and is placed after every other kept fact.
- Facts that are not disputed are sorted in three steps: `confirmed`, then `single` with at least
  one non-weak source, then `single` with weak sources only (`fact_priority` in
  `app/services/fact_selection.py`). The trust step stays the first key. Inside a step
  (`rank_order`): relevance 3, then 2, then 1; among the facts of one relevance of 2 or more the
  aspects take turns (the first fact of each aspect, then the second of each, and so on, in the
  model's order), so ten facts on one aspect do not fill the limit; facts of relevance 1 follow in
  the model's order. Without a ranking every fact counts as relevance 2 of one aspect and the order
  is the model's order inside a step, as before. A weak fact is never removed for being weak, it
  only goes lower.
- **Domain cap.** `FACTS_MAX_PER_DOMAIN` (6) limits how many places one domain takes among the
  facts that are not disputed. Walking the sorted list, a fact is accepted if at least one of its
  domains has room, and it is charged to the least loaded of its non-weak domains (of all its
  domains if every one is weak), the first in support order on a tie. So a fact that stands on
  Wikipedia and on a news site is not pushed out because Wikipedia is full. Facts that do not fit
  are set aside, and the next facts of other domains take their places.
- The first `FACTS_MAX_FACTS` (20) of the accepted facts are kept.
- **The cap yields to the floor.** If fewer than `max(FACTS_MIN_FACTS, THREAD_MIN_FACTS)` facts
  are kept (5 by default; never more than `FACTS_MAX_FACTS`), the facts set aside come back in
  priority order until the floor is reached. This way the cap never turns a requested thread into a
  short post and never alone causes `InsufficientFacts`. The floor does not invent facts: a set
  that is short without the cap stays short.
- Disputed facts are always kept, on top of the limit, so the user sees them.
- Attributed facts (not disputed) never enter the sort, the cap or the limit: they are kept on top
  of the limit, like disputed facts. A rebuttal cut by the limit or the cap comes back with its
  claim (`rebuttals_restored`), so a rebutted claim is never shown without its rebuttal.
- The output is the kept facts, then the rebuttals that came back, then the attributed facts, then
  the disputed ones; ids `F1`, `F2`... follow this order.
- Disputed facts ignore the cap and do not take its places.
- If fewer than `FACTS_MIN_FACTS` (3) assertable facts are kept (not disputed, not attributed; a
  rebuttal that came back counts), the result is `InsufficientFacts` with whatever survived.
  Disputed and attributed facts never count towards the minimum, because they cannot be stated.
  Code never fills the gap.
- The `ExtractionStats` of the weak and cap rules, counted over the verified facts before the cut:
  `support_weak`, `facts_weak_only`, `facts_lost_confirmed_by_weak` (would be `confirmed` by all
  domains, is `single` without the weak ones), `facts_cut_by_domain_cap` (left out by the cap, net
  of the facts that came back), `facts_domain_cap_restored`. Counters only, never domains or texts.
- The relevance counters: `relevance_scored` (facts with a valid entry), `relevance_dropped`,
  `relevance_failed`, `relevance_period_overruled`, `facts_about_source`, `facts_outside_period`
  (after the code check), `facts_unrelated` (relevance 0), `facts_set_aside_off_topic` (net of the
  facts the floor brought back) and `facts_off_topic_restored`. The facts set aside are not in the
  `FactSet`; only these counters and the INFO line show them.
- The stance counters: `facts_claimed`, `facts_rebutted` (over the verified facts),
  `facts_stance_unmarked`, `facts_stance_upgraded_by_code`, `facts_stance_role_swapped`,
  `facts_unknown_stance`, `rebuttals_proposed`, `rebuttals_verified`
  (linked to their claim), `rebuttals_restored`. `candidates` counts the top-level facts of the
  reply; rebuttals are counted apart.
- At most 40 candidates from the model are used, the first ones. The prompt states the bound at its
  start and again in its last rule, with the number from `FACT_CANDIDATES_MAX`. The reply may use up
  to `FACT_EXTRACTION_MAX_TOKENS` (12000) output tokens. The model is asked for at most
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
- **Attributed claims are a separate block** (HIS-32), between the facts to state and the disputed
  ones: `F4 [claimed]: text` and `F5 [rebutted; rebuttal: F3]: text` (`none among the facts` if the
  rebuttal is not in the set). The rule: never state one as fact; a claimed one only with its
  attribution or not at all; a rebutted one only together with its rebuttal, as a myth and what is
  known instead. "Which facts may be stated" has one definition, `assertable_facts` in
  `app/prompts/writing.py` (not disputed, not attributed): the short selection, the long minimum,
  the unused list of the expand correction, the thread threshold and the bot all use it.
- **A short post gets a selection of the facts.** Before the call, code picks at most
  `SHORT_MAX_FACTS` (3) facts for `short` (`select_short_facts`, `app/services/short_post.py`);
  `long` and `thread` get the whole `FactSet`. The rules:
  1. Assertable facts (not disputed, not attributed), `confirmed` first, then `single`, in
     `FactSet` order inside each status. A short post never gets an attributed claim: three
     sentences have no room for the attribution and the rebuttal. Step 3 orders the facts by
     trust, then by relevance and aspect (see "Relevance"), so a fact about a source never opens
     the list and the first facts come from different aspects. The length of a fact's text is not used: short facts are mostly side details.
  2. A dispute enters only whole and only if it fits into the slots left. Overlapping disputes
     merge into one unit; disputed facts with no group form one unit. With the default limit and 3
     or more facts to state, a short post gets no dispute.
  3. If no fact to state was picked and no unit fits, the first unit is taken whole, over the
     limit: a dispute is never given half.
  The prompt gets only the selection; the `FactSet` itself does not change, so the user still
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
  1. Candidates are the disputed and attributed facts of the set that went into the prompt: the
     selection for `short`, the whole `FactSet` for `long` and `thread`. A disputed fact is one with
     status `disputed` or in a `Dispute`; an attributed one has stance `claimed` or `rebutted`. So
     the bot marks a version the text states even when the model did not report it.
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
- **Long post minimum (HIS-30).** `long` has a floor as well as a ceiling: `LONG_MIN_CHARS`
  (1200, about 3 paragraphs of 400) and `LONG_MIN_USED_FACTS` (6). The facts minimum is capped by
  the number of facts that can be stated in the prompt (not disputed), so a small set is never
  asked for the impossible. `LONG_MAX_CHARS` stays a ceiling, not a target. The format rule gives
  numbers derived from the settings, like the short budget: at least `max(2, min // 400)`
  paragraphs, "usually `min` to 2 x `min` characters" (`LONG_TYPICAL_SIZE_MULTIPLIER`) so the model
  does not sit exactly on the floor, never fewer than `min`, develop at least N facts, and do not
  shrink the post to a summary. With both minimums at 0 the old open rule is used.
  A draft under the floor is a length problem and goes through the same retry as an overlong one
  (`WRITING_LENGTH_RETRIES`, one retry for `long`). The correction states the characters and the
  facts used against the minimums, asks to expand the text using more of the facts, and lists the
  ids of the facts that can be stated and are not used yet (ids only, never texts). If the second
  reply is still under the floor, the `Draft` is returned with `LengthIssue.TOO_SHORT`
  (characters, part 1) and/or `LengthIssue.TOO_FEW_FACTS` (used facts), and the bot warns. The
  post is never lost. The minimum applies to `long` only; `short` and `thread` are unchanged.
  The fact count is `used_fact_ids` as step 4 builds it, so it relies on the ids the model
  reported (plus the disputed facts that code finds); only the character count is checked by
  code against the text.
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
   the text as written. Overlapping matches of one rule count once ("это не просто X, а Y" and
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
  sees them, and the attributed claims block of the writer, with each stance. The whole `FactSet`,
  not the short selection: a claim is supported if any fact states it. Never quotes or URLs.
- **What it looks for:** `unsupported_claim` (a conclusion, cause, consequence or claim of
  importance no fact states; an added qualifier such as "по преданию"; an added precision or
  emphasis such as "точно", "уже"; a computed interval; a disputed fact stated as established; an
  attributed claim stated as fact, or a rebutted one without its rebuttal),
  `ambiguous_reference` (a pronoun or omitted subject that makes the sentence claim something the
  facts do not), `filler`, `opinion` (over `OPINION_MAX_PER_POST` or as a closing line), `cliche`,
  `triplet` (rhetorical only; a list from the facts is not one) and `invented_experience`.
- **Not its job:** honest retelling of a fact in other words (an explicit exception in the prompt),
  cautious wording of a dispute, the attribution an attributed claim carries («По преданию»),
  punctuation, emoji, hashtags, the closing question, length, digits,
  and the hook, rhythm and thread structure (rules 7, 8, 16 are not enforced automatically).
- **Reply:** `findings`, each `excerpt`, `rule`, `explanation` (Russian, for the user, at most
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
6. **A regression is rejected (HIS-30).** A new version is compared with the last accepted one:
   the total characters of its parts (numbering excluded) and `len(used_fact_ids)`. It is a
   regression if it keeps less than `STYLE_MIN_RETAINED_CHARS_RATIO` (0.6) of the characters and
   has lost more than `STYLE_REGRESSION_FREE_CHARS` (100), or keeps less than
   `STYLE_MIN_RETAINED_FACTS_RATIO` (0.6) of the used facts and has lost more than
   `STYLE_REGRESSION_FREE_FACTS` (1). The allowance is for a short post: removing one flagged
   sentence (under 100 characters, one fact) is a legitimate shortening, never a regression. A
   previous version with no used facts has no fact regression. A regression is not evaluated (no
   critic call), never enters the candidates and never becomes "the previous version" of the next
   attempt; it is counted in `StyleResult.regressions_rejected`. It uses one regeneration of the
   budget. The next attempt starts from the same last accepted version and the same violation list
   and adds "The previous attempt removed too much: keep all the text and all the facts, and change
   only the flagged fragments". If the budget is used up, the best accepted version goes out (at
   worst the original) with its remaining violations; the bot says how many were rejected.
   The regeneration instruction itself says: fix only the flagged fragments, the rest stays word
   for word, the same length, the same facts.
7. **The best version goes out:** the fewest violations of the rules in `DANGEROUS_STYLE_RULES`
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
  regeneration failed, the number of rejected regressions, the critic status of each attempt, the number of violations, dangerous ones,
  counts per rule name, and the dropped, withdrawn and over-limit findings. Texts, excerpts and
  explanations are never logged.

A rejected regression costs one `write_draft` call and no critic call, and counts in the same
limit `STYLE_MAX_REGENERATIONS`.

Worst case per post: 3 critic calls and 2 `write_draft` calls, each of which may make up to 3 writer
calls for a short post (the length retries).

### 6. Delivery

Input: the accepted `StyleResult` and its `FactSet`. Output: Telegram messages. The orchestration
of steps 1-5 is `Pipeline` in `app/services/pipeline.py`; the Telegram side is `app/bot`.

#### The run

`Pipeline.run_topic(topic, post_format, progress)`:

1. No research source enabled: `NoSources`, before any call.
2. Steps 1-3. No snippets: `ResearchFailed` if every source failed at least once, otherwise
   `NothingFound`. `InsufficientFacts` becomes `NotEnoughFacts` with the counts of assertable and
   disputed facts and the facts themselves.
3. The format is the one the user asked for (a prefix `тред:`, `лонг:`, `коротко:` in the
   message) or `POST_DEFAULT_FORMAT` (`short`). A thread with fewer than `THREAD_MIN_FACTS` (5)
   assertable facts (not disputed) becomes a short post and the outcome carries a
   `ThreadDowngrade`, which the bot shows as a warning.
4. The `FactSet` is stored, then steps 4 and 5 run with the examples loaded from `EXAMPLES_DIR` on
   this request (an edit in `data/examples` works without a restart), no angle and
   `allow_closing_question=False`. The chosen draft is stored and `PostReady` is returned with the
   draft id, the `StyleResult`, the research failures, the `ExtractionStats` and the buttons that
   make sense for it.

`progress(stage)` is awaited before each stage (planning, research, facts, writing, style). An
`LLMError` on any step becomes `StepFailed(stage, kind)`; the kind is the error class (config, auth,
request, rate limit, unavailable, invalid response). The critic and regeneration failures of step
5 are already handled inside it and never reach this point. Any other exception propagates.

#### Messages

In this order:

1. **The post**, as plain text with no `parse_mode`, so it copies without markup: one message for
   `short` and `long`, one message per tweet for a thread (`Draft.rendered`, numbering included).
   A message over Telegram's 4096 characters (only a `long` post can reach it) is split at
   paragraphs; a paragraph over the limit at sentences (`split_sentences`), a sentence over it at a
   space, a word over it hard. Nothing is added to the pieces, so they copy as the post. The
   buttons are under the last message of the post.
2. **Warnings**, plain text, only if there are any: a thread turned into a short post, numbers not
   among the facts (`unverified_numbers`), length violations with the numbers, the remaining style
   violations (rule, excerpt, tweet, explanation; `length` and `unverified_number` are shown by the
   first two lines and not repeated), the critic did not check the text (`critic = failed`), a
   regeneration failed and the best version is shown, regressions rejected (a count and the
   reason, never the rejected text), a draft under the long minimum (characters or facts against
   the minimum, from the `length` lines), and a non-empty `dropped_tail` with the
   dropped text and a note that `used_fact_ids` may be inexact, and the ids of the versions and
   rebutted claims the post uses, with a reminder to check they are told with the attribution.
3. **Facts**, HTML with every text escaped. Each fact: id, status ("подтверждён: разные домены",
   "один источник" or "СПОРНО"), text, and a link per source url labelled with its host. A
   disputed fact has "Почему спорно (вместе с F6): explanation" from its `Dispute`, or "источники
   расходятся" if it is in no group. An attributed fact adds its stance to the status: "один
   источник · версия" or "· опровергнуто"; a rebutted one has "Опровергается: F3" (or a line that
   its rebuttal failed the check), and the rebuttal has "Опровергает: F5". "Фактов мало" counts the
   versions and rebutted claims apart from the disputed facts. After the first post the facts are split into "В посте" (by
   `used_fact_ids`, which since HIS-23 includes the disputed facts the text states) and "Не вошли
   в пост", every fact of the `FactSet` is shown, and partial source failures are noted at the
   bottom as source and error class only. After a button only the facts of the new variant are
   shown, with "Остальные факты (N) в первом ответе". Messages are cut between facts, never inside
   one; link previews are off.

The progress is one message, edited when the stage changes (at most 5 edits a run, under Telegram's
edit rate). It is deleted after a post is delivered; on any other outcome it is edited into the
message for the user. A failed edit or delete is logged and ignored. A `RetryAfter` while
sending is waited out once.

#### Buttons

| Button          | What runs                                                                       |
| --------------- | ------------------------------------------------------------------------------- |
| короче          | Same format and angle, a `Revision` asking for a shorter version that keeps the main facts |
| в тред          | `PostFormat.THREAD`, a `Revision` with the previous text. Not shown under a thread or when there are fewer than `THREAD_MIN_FACTS` assertable facts |
| другой заход    | The next angle of `ANGLES` (`app/prompts/revisions.py`), in turn, and a `Revision` asking for another first sentence and order |
| ещё вариант     | Same format and angle, a `Revision` asking for other wording                     |

`Pipeline.rework(draft_id, action, progress)` reads the stored draft and its `FactSet`, re-enters at
step 4 and runs step 5 on the result exactly as for the first post. Research is never re-run, so a
click is cheap and the facts the user checked stay the same. A missing draft (restart, eviction)
is `DraftExpired`; "в тред" on too few facts is `ThreadUnavailable`, with no model call.

Each angle says it sets only the presentation and the order of the facts, that every claim still
comes from the facts, and that if the facts do not fit it (no person named, for example) the model
takes the closest presentation they allow and invents nothing. The angles: through a person, through
a detail or a number, through a place, and through a comparison of two facts from the list without
any conclusion about a causal link between them. A short post always gets the same 3 facts
(`select_short_facts`), so "другой заход" of a short post changes the presentation, not the facts.

`callback_data` is `d:<action>:<draft id>`, the id being 12 random hex characters, at most 25
bytes. The id is random rather than a counter so a button from before a restart never lands on a
draft of a new run.

#### State

`RunStore` (`app/services/run_store.py`) is a Protocol with async methods: `add_run(fact_set)`,
`add_draft(run_id, draft, angle_index)` (none if the run is gone) and `get_draft(draft_id)`. Today
it is `InMemoryRunStore`: the last `STATE_MAX_RUNS` (20) runs, least recently used evicted first,
each with at most 30 drafts. HIS-9 replaces it with SQLite behind the same Protocol. After a restart
a button answers "кнопки устарели" instead of failing.

#### Concurrency, timeout and input

- aiogram polling runs every update as a task (`handle_as_tasks=True`, set explicitly), and a
  handler only starts a background job and returns, so other updates are handled while a run goes.
- One job at a time per user (`JobRunner`, `app/bot/jobs.py`). A topic or a button during a job is
  answered at once with "ещё работаю" and dropped, not queued. A button is answered (`answer()`)
  before any work.
- A job is cancelled after `PIPELINE_TIMEOUT_SECONDS` (600) and the user is told; they are
  free again either way, including after an unexpected error.
- A message starting with `/` other than `/start`, and any message without text (photo, sticker,
  voice, document), gets a short hint and starts nothing. A topic over `TOPIC_MAX_CHARS` (500), or
  a prefix with nothing after it, is an input error with its own reply: the topic goes into prompts
  and into the planned search queries.

#### Logs

The topic is the user's data: INFO logs only its length and the requested format. One INFO line
per run or button holds the outcome class. An unexpected error is logged with its class and the
stack, never its message, because a `ValidationError` message carries input values. Texts of
topics, posts, facts and quotes never reach the log.

## Data models

All of them are Pydantic v2 models and live in `app/domain/`.

| Model               | Fields and meaning                                                                                       |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| `Snippet`           | A piece of source text as returned by a research source: id, origin, title, URL, text, language          |
| `SourceRef`         | Evidence for a fact: `snippet_id`, `url`, `domain` (after the domain rules), the verified verbatim `quote` |
| `Fact`              | `id` (`F1`...), `text` in Russian, `support` (one or more verified `SourceRef`), `status`, `stance` (default `asserted`, so older snapshots load), `rebutted_by` (ids of the rebutting facts) |
| `ClaimStance`       | `asserted`, `claimed`, `rebutted`: how the source presents the claim                                      |
| `FactStatus`        | `confirmed`, `single`, `disputed`                                                                         |
| `Dispute`           | `fact_ids` (2 or more) and an `explanation` in Russian for the user                                     |
| `FactSet`           | `topic`, `facts`, `disputes`                                                                              |
| `ExtractionStats`   | Counters only: snippets, candidates, support proposed and dropped by reason, facts dropped by reason, disputes, facts cut by the limit, the relevance ranking and the facts set aside as off the topic |
| `FactsExtracted`    | `outcome = extracted`, the `FactSet` and the stats                                                         |
| `InsufficientFacts` | `outcome = insufficient_facts`, the `FactSet` of what survived, `assertable_count`, `required`, the stats |
| `FactExtraction`    | `FactsExtracted \| InsufficientFacts`. A caller has to tell them apart; step 4 accepts only `FactsExtracted` |
| `PostFormat`        | `short`, `long`, `thread`                                                                                 |
| `DraftPart`         | One part of a post: `text` as the model wrote it and a `prefix` added by code (numbering); `rendered` is the two joined |
| `LengthViolation`   | `issue` (`part_too_long` or `too_many_parts`), the 1-based `part` or none, `actual` and `limit`            |
| `SentenceBudget`    | `max_sentences` and `sentence_chars` of a short post, derived from `SHORT_MAX_CHARS` and `SHORT_SENTENCE_CHARS` |
| `Revision`          | `instruction` and `previous` (the texts of the previous draft's parts), for "короче" and "ещё вариант"    |
| `Draft`             | `post_format`, `parts` (exactly one for `short` and `long`), `used_fact_ids`, `unverified_numbers`, `length_violations`, `attempts`, `dropped_tail` (pieces a tail drop removed, empty by default). `length_violations` also holds `too_short` and `too_few_facts` for a long post under its minimum. `texts` gives the parts without numbering, the one input for the style filter; `rendered` gives what the user copies |
| `StyleRule`         | The rules a violation names: `dash`, `banned_phrase`, `invented_experience`, `emoji`, `hashtag`, `closing_question`, `length`, `unverified_number` (code) and `cliche`, `triplet`, `filler`, `opinion`, `unsupported_claim`, `ambiguous_reference` (critic; `invented_experience` too) |
| `Violation`         | `rule`, `source` (`code` or `critic`), `part` (1-based or none), `excerpt` (from the text, none for length), `explanation` in Russian for the user |
| `StyleReport`       | `violations`, `critic` (`checked`, `disabled`, `failed`), counters of critic findings dropped (excerpt not in the text), withdrawn and over the limit; `passed` is no violations |
| `StyleResult`       | The `draft` that goes out, its `report`, `attempts` (versions evaluated), `chosen_attempt`, `regenerations`, `regeneration_failed` and `regressions_rejected` (regenerations the loop rejected as too destructive) |
| `PipelineStage`     | `planning`, `research`, `facts`, `writing`, `style`: what the progress message shows and what a failure names |
| `PostAction`        | The buttons: `shorter`, `thread`, `angle`, `variant`                                                      |
| `FailureKind`       | The class of an `LLMError` for the user: `config`, `auth`, `request`, `rate_limit`, `unavailable`, `invalid_response`, `other` |
| `StoredDraft`       | What a button needs: `draft_id`, `run_id`, the `FactSet`, the `Draft` and the `angle_index` it was written with |
| `PostReady`         | `draft_id`, the `StyleResult`, the `FactSet`, the buttons to show, `variant`, research `failures`, an optional `ThreadDowngrade`, the `ExtractionStats` of the first post |
| Other outcomes      | `NoSources`, `ResearchFailed`, `NothingFound`, `NotEnoughFacts` (the `InsufficientFacts`, the disputed count, failures), `StepFailed` (stage, kind), and for a button `DraftExpired` and `ThreadUnavailable`. `TopicOutcome` and `ReworkOutcome` are their unions |

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
| How a source presents a claim (asserted, claimed, rebutted) | Model  | Needs language understanding                              |
| How well a fact answers the topic, its aspect, whether it is about a source | Model | Needs language understanding; code validates ids and range |
| A fact flagged outside the topic's period has only years outside it | Code | Year extraction and a range with a margin; an unconfirmed flag is dropped |
| The attribution or rebuttal is visible next to the quote | Code      | Stem match of the markers in the quote's sentence and its neighbours |
| An attributed claim is not stated as fact in the post   | Model      | The critic reads the post against the attributed claims block |
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
post still goes out with it listed, and the user decides.

## Failure behaviour

- A source is down: skip it, continue, note the failed source and error class under the facts.
  Every source down: the user is told, nothing is written. No source enabled: the user is told
  before any model call.
- Fewer than `FACTS_MIN_FACTS` facts that can be stated survive step 3: the step returns
  `InsufficientFacts`, the pipeline stops and says so. Do not write a post from the topic alone.
- Fewer facts than a thread needs (`THREAD_MIN_FACTS` assertable facts): a short post is written
  instead and the warning says so; the "в тред" button is not shown, and an old one answers
  without a model call.
- The critic fails: the post goes out with the deterministic findings and `critic = failed`, and
  the user is told the critic did not check it. A failed regeneration keeps the best version
  already written. See step 5.
- An LLM error on a step: the user is told which step failed and why in one phrase (no request
  details); the log has the step and the error class.
- A run over `PIPELINE_TIMEOUT_SECONDS`: cancelled, the user is told.
- The provider for a step is unavailable: the log names the step and the provider, the user sees
  the step. There is no
  silent fallback to the other provider, because that would change the cost and the voice
  without the user knowing.
