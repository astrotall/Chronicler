# Decisions

What is decided, and what is still open. The locked decisions are the table in
[CLAUDE.md](../../CLAUDE.md). This file holds the reasons, and the questions that are not yet
closed. A decision on an open question is recorded here in the same change that acts on it.

## Decided

| Decision                                  | Reason                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------- |
| One user, ID whitelist                    | The bot serves one user. Multi-user means accounts, quotas and isolation, none of it needed |
| No publishing to X                        | The user reviews and posts by hand. It removes a whole class of risk and an API dependency |
| Post only from facts with sources         | The main failure of an LLM here is an invented date, number or quote                         |
| Verbatim quote per fact, checked by code  | A model cannot certify its own quote. A substring check can                                  |
| Numbers and dates in the post checked by code | Catches the writing step inventing a figure the facts do not contain                      |
| Anthropic and DeepSeek behind one interface | Cost and voice differ per provider and may differ per step. Cheap to start, cheap to switch |
| Provider chosen per step, start with DeepSeek | Planning and extraction are mechanical, writing and critique are where voice matters      |
| No database first, SQLite later           | Early iterations do not need persistence. SQLite is a file and needs no service              |
| PostgreSQL, Redis, Arq, Alembic, Docker deferred | One user and one process do not need them. Each needs its own ticket with a stated reason |
| Fact status `confirmed` / `single` / `disputed` | The user sees how well a claim stands, and disputed claims are written cautiously    |
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
| A weak domain is not a blocked domain | `RESEARCH_BLOCKED_DOMAINS` removes a page before extraction. `FACTS_WEAK_DOMAINS` keeps it as a visible source that does not count as independent. Video, blogs, school slides and AI slide makers copy each other, so a pair of them is not a confirmation |
| Weakness is matched on the host, not on the registrable domain | The domain is the last two labels, so `otvet.mail.ru` would become `mail.ru`. The host keeps the distinction |
| `confirmed` needs two non-weak domains; weak facts go lower, not out | Dropping them would hide what the sources said. Ordering and the "слабый" mark let the user see and judge |
| A per-domain cap of 6 of 20, yielding to the larger of the fact and thread minimums | One archive page gave 12 of 20 facts. The floor keeps the cap from turning a thread into a short post |
| A fact is charged to its least loaded non-weak domain | "First non-weak domain" would push out a confirmed fact because Wikipedia, usually the first support, is full |
| Domain outside groups is the last two host labels | No public suffix list without a new dependency. Merging `x.co.uk` and `y.co.uk` errs towards `single`; the full host would err towards a false `confirmed` |
| The stance of a claim is a field of the fact (`asserted`, `claimed`, `rebutted`), with the attribution also in its text | HIS-32. Code and every later step read the field; the text alone cannot be checked. See "Stance of a claim" below |
| An unknown stance drops the fact; a missing one is `asserted` | Reading an unknown value as `asserted` is the bug itself, as `claimed` it trusts an unchecked attribution. Missing is the pre-HIS-32 behaviour and avoids a retry of the whole extraction |
| A `claimed` or `rebutted` fact without a marker next to its quote is lowered to `asserted` | The owner's decision. The check stops the model from calling a fact a myth from its own knowledge; the list of markers is broad, and live runs list every lowering |
| A rebuttal is linked by `rebutted_by`, not merged into a `Dispute` | A `Dispute` would make the true rebuttal "СПОРНО" and tell the writer "sources disagree" when the same source says the claim is a myth. The owner's decision |
| Strong markers raise an asserted fact to `claimed` by code; weak markers only confirm the model's stance | HIS-32 round 2, the owner's decision: the duel of Peresvet stayed asserted when the model merged an attributed quote with a plain one. See "Stance of a claim" below |
| A `rebutted` fact whose quote sentences open with a rebuttal opener is the rebuttal and becomes `asserted` | The model swapped the claim and its rebuttal live («Задонщина»). The owner's decision |
| `stance` is written only for claimed and rebutted facts | Output tokens. A missing stance already reads as `asserted` |
| `FACT_EXTRACTION_MAX_TOKENS` 12000 | The worst live reply was 7987 of 8000; 12000 leaves 50% over it. DeepSeek allows up to 384K output for `deepseek-flash` |
| Attributed claims are kept beyond the limit, never `confirmed`, never assertable | Like disputed facts: the user sees them, they never count towards a minimum, and a short post never gets one |
| Facts that may be stated are ranked against the topic in a third pass of `fact_extraction` (HIS-27) | The model's order follows the snippets, not the topic, and the limit and the short selection took the first facts. See "Topic relevance" below |
| Trust first, relevance inside a trust step, aspects take turns among relevance 2 and 3 | The trust steps of HIS-28 stay the first key. Turns keep one aspect (ten ration norms, eight Stakhanov facts) from filling the limit; relevance 1 follows in the model's order. The owner's decision |
| A fact off the topic (about a source, outside the period, relevance 0) is set aside over every trust step and comes back only for the floor | A well sourced fact about an exhibition is still not about the subject. The floor keeps the ranking from causing `InsufficientFacts` or a short post instead of a thread. The owner's decision |
| `about_source` is narrow: the name, the definition, the history of the subject, its memory and who reports it are about the subject | The owner's decision. The live run lost «Епифаний ... сообщает» and «По оценкам историков» to the flag before the prompt got neutral examples of both kinds |
| `outside_period` stands only if code finds every year of the fact outside the topic's years ± 10 | The model set it on «отменят уже в 1935 году» for the 1930s and on a topic with no period. Decided by the implementer after the first live run |
| A failed ranking keeps the model's order | The ranking improves the order; losing an extraction to it would cost a full extraction. `relevance_failed` is counted |
| Snippets are shown to the model as `S1`, `S2`... | A 16-character hash is easy to garble. An unknown label drops the support item, nothing is guessed |
| Fact text in Russian, quote in the snippet's language | The post is Russian; a translated quote could not be checked. The translation itself is guarded by the number check |
| Numbers of a fact must occur in its quotes | Step 4 checks the post's numbers against the fact text, so the fact text must be bound to the source too |
| Quote check normalises typography, not wording | See "Quote normalisation" below |
| One extraction call within a character budget, no batches | Grouping one claim across snippets needs all snippets in one call. The budget cuts long snippets to a common cap, see pipeline.md |
| Contradictions are a second pass on the `fact_extraction` step | Same kind of reading, same provider. It sees only fact texts, before the limit |
| A contradiction needs an explicit verdict from the model | Without it DeepSeek reported sequences of events as contradictions, even while writing "this is not a contradiction" |
| "Not enough facts" is a typed result, not an exception | `FactsExtracted \| InsufficientFacts` makes the writing step handle both, and the user still sees what was found |
| Extra candidates are dropped, not rejected | The model sometimes returns more than the 40 asked for. A retry repeats a whole extraction; dropping the tail loses nothing that was verified |
| Git hooks: `make check` and `make test` both in pre-commit, no pre-push | See "Git hooks" below                                                    |
| The topic is a frame for the writer, not a source | It tells the model what the post is about and what could hook. Every claim, number and date comes from the facts; a number only in the topic is reported as unverified |
| The writer sees fact ids, texts and statuses, never quotes or URLs | The post is written in the model's words, not copied from a source |
| Disputed facts go to the writer in their own block | A disputed fact is never in the list of facts to state, so the model cannot state it flatly by mistake |
| An unverified number regenerates the post in step 5 (HIS-7) | Step 4 only lists it; style rules say a figure not among the facts is rejected. See "Style filter" below |
| One writing method for every button      | Angle and revision are optional inputs of `write_draft`; a button re-enters step 4 without new code paths |
| The writer replies in JSON, fact ids first | `complete_json` validates the reply. Ids first make the model pick facts before it writes; the text is the last field |
| Length: one retry with the exact problems, then deliver marked | See "Length limits" below. For `long` and `thread`; a short post has its own rules, see "Short post length" |
| A short post is written from at most `SHORT_MAX_FACTS` (3) facts picked by code | The model was asked to fit 6 to 10 facts into room for 2 or 3 and used 6 to 8. See "Short post length" below |
| Short selection: `confirmed` before `single`, `FactSet` order, a dispute only whole | Code, no LLM. The extraction order puts core facts first; fact length would favour side details. Half a dispute would state one version as established |
| A thread is written from at most `THREAD_MAX_FACTS` (10) asserted facts picked by code, plus up to `THREAD_MAX_ATTRIBUTED` (2) attributed units | In HIS-40 the writer was offered 20 to 24 facts, used 17 to 23 and wrote 9 to 13 tweets, a list of facts. The limit has to come from code, as for `short`. See "Thread fact cap (HIS-42)" below |
| Thread selection: `FactSet` order, no re-sorting; attributed units (a disputed group, a claimed fact, a rebutted fact with its rebuttal) in `FactSet` order of their first member, outside the cap | Code, no LLM. Step 3 already ranked the facts. Without an allowance the thread would lose every dispute and every legend; a unit is never split |
| Disputed facts a draft states are added to `used_fact_ids` by code | The model left both numbers of a dispute out of `fact_ids` in 3 of 4 Kulikovo threads, so the bot would have shown the dispute unmarked. See "Used facts and disputes" below |
| A dispute is marked whole in `used_fact_ids` | Marking one version as used and not the other would show half a dispute as ordinary |
| Short budget in sentences derived from the limit, not "aim below" | A model does not count characters. `SHORT_MAX_CHARS // SHORT_SENTENCE_CHARS` sentences of up to `SHORT_SENTENCE_CHARS` characters |
| Short retry quotes the sentences to cut and the excess, up to 2 retries | "Shorten it" made the model rewrite and sometimes grow the text. `SHORT_LENGTH_RETRIES`; `long` and `thread` keep one retry |
| Tail drop of a short post exists but is off by default | See "Short post length" below |
| Style rule data in `app/config/style.py`, one source | The writing prompt renders its rules from it and the style filter reads it. Not an env variable: a phrase contains a comma |
| Writing prompt: English structure, Russian style rules | Matches the other prompts. The rules block is in Russian with the phrases it bans. A fully Russian prompt is to be tried if the voice reads wooden |
| Voice rules 13-16 and the opinion cap from the first live drafts | See "Voice rules from live drafts" below |
| A first-person opinion: at most 1 per post or thread, in config | "Allowed" was read as "expected": an opinion closed every second tweet. `OPINION_MAX_PER_POST` in `app/config/style.py` |
| Dates, years, terms, sums, sizes, ages and percentages in digits; small counts may be words | Digits are what the number check sees. "два войска" reads naturally and is not a risk; the risk is a computed interval ("через два года"), which the rule forbids when no fact states it |
| The thread format rule no longer says "each tweet reads on its own" | It pushed the model to close every tweet with a comment. Replaced by "may be one short sentence, needs no closing line, is never a fragment" |
| Em and en dash forbidden, a spaced hyphen " - " replaces them | The owner's decision. "Replace with a full stop, a comma or a colon" made the model put an awkward comma where the dash was. The rule says the spaced hyphen replaces a dash only, so it does not become a default punctuation mark |
| Style filter: code checks, then an LLM critic, then up to 2 regenerations | See "Style filter" below |
| The filter never edits the text; a regeneration is `write_draft` with a `Revision` | One writing path for every change. The text the user gets is always the writer's, checked again |
| The critic runs even when code already found something | One regeneration then fixes everything at once |
| The critic quotes an excerpt, and code checks it occurs in the post | The critic can invent a problem in a sentence the post does not have |
| A critic finding has a verdict after the explanation | The same device as for contradictions: the model reasons first and can withdraw a finding |
| A critic or regeneration failure never loses the post | The deterministic checks already ran; the post goes out flagged (`critic = failed`, `regeneration_failed`) instead of the bot failing |
| A regeneration that keeps too little of the text or the facts is rejected, never chosen | HIS-30: the filter compared violations, so a one-sentence version with no violations won. See "Long post size and the regression guard" |
| A length violation alone does not regenerate | Step 4 already spent its retries on it. The owner's decision |
| The best version: fewest dangerous violations, then fewest in total, the later on a tie | An unsupported claim is worse than a cliche. The dangerous rules are data, `DANGEROUS_STYLE_RULES` |
| Hook, rhythm and thread structure are not checked | Subjective; a critic verdict on them would drive regenerations by taste. Prompt and few-shot only. The owner's decision |
| No deterministic triplet check | A list of names from the facts is legitimate. The critic checks rhetorical triplets only |
| Phrase matching by stems, no morphology library | No new dependency. Words in `BANNED_PHRASE_EXACT_WORDS` match whole, against false positives such as "в заключении мира" |
| "не просто X, а Y" banned with or without "это" | The owner's decision: "Он был не просто город, а крепость" is the same frame |
| Default format `short` (`POST_DEFAULT_FORMAT`) | The cheapest first answer, and the only one under which all four buttons make sense. A prefix `тред:`, `лонг:`, `коротко:` picks another format for one topic |
| A thread needs `THREAD_MIN_FACTS` (5) assertable facts | From 3 facts a thread is 2 tweets, not a thread. Below the threshold a short post is written and the user is told; "в тред" is not offered. See "Bot delivery" below |
| The post goes out as plain text, the facts as escaped HTML | The post must copy without markup. The facts need links; HTML with `html.escape` keeps them short, and facts are split only between whole facts |
| A long post over 4096 characters is split, not sent as a file | Copying from a file is clumsy on a phone. Paragraphs, then sentences, then spaces; nothing is added to the pieces |
| State in memory behind the `RunStore` Protocol, `STATE_MAX_RUNS` (20) runs | No database yet (locked). HIS-9 replaces the store with SQLite without touching the pipeline or the handlers |
| Draft ids are random, not counters | After a restart a counter would map an old button onto a new draft. A random id misses and the user is told the buttons are stale |
| One job per user, a new request during it is refused, not queued | One user; a queue would hide that a long run is still going. Buttons are answered at once |
| A run is cancelled after `PIPELINE_TIMEOUT_SECONDS` (600) | See "Bot delivery" below |
| A topic is at most `TOPIC_MAX_CHARS` (500) characters; commands and non-text messages get a hint | The topic goes into prompts and, through the plan, into search queries. A pasted article is an input error, not a topic |
| "другой заход" rotates through fixed angles in `app/prompts/revisions.py` | An extra model call to invent an angle costs a call and is unpredictable. Each angle says it shapes only presentation and order |
| The topic is logged by length only | It is the user's data, like the post |

## Details of decided questions

### Voice rules from live drafts

The first live drafts (HIS-6, DeepSeek, the Kulikovo facts, no examples) closed almost every
tweet with a line that commented on the fact before it or rated its importance, put a
first-person opinion in every second tweet, wrote "через два года" past the number check, and
replaced a dash with an awkward comma. HIS-21 added rules 13-16 to the rules block, rewrote rules
1, 2 and 4 (see [style-rules.md](style-rules.md)), and told the writer that a cause, a consequence
or a claim of importance no fact states is a new claim.

Measured with `tests/test_generator_live.py` on two hand-written fact sets (Kulikovo, Apollo 11),
two samples per topic and format, before and after, no examples in both:

- Filler closers and claims of importance: in all 4 threads before (no short post had one),
  none after.
- First-person opinions: 4 in 8 samples before, none after.
- Numbers in words outside the check ("через два года"): 2 before, none after.
- Dash replaced by a comma: 1 before, none after. Rule 4 then changed: the owner allowed a
  spaced hyphen in place of a dash and dropped "replace with a full stop, a comma or a colon".
  The new wording has not been measured live.
- Not improved: threads are still mostly one fact per tweet, now without the closer, and the text
  reads drier, closer to the facts' own wording. Zero opinions may be an over-correction. Short
  posts went over 280 characters after the retry in 2 of 4 samples before and 3 of 4 after; that
  is a separate problem, addressed in HIS-22 (see "Short post length").

Two samples per cell are noise-level evidence. The voice is still meant to come from the examples.

### Used facts and disputes

Found in the HIS-21 live runs: in 3 of 4 Kulikovo threads the text named both disputed numbers
(60 000 and 150 000) and the model did not list the disputed facts in `fact_ids`. The number check
passes this, because it compares with the texts of all facts. The bot (HIS-8) marks disputed facts
in its reply with "СПОРНО"; if it relied on `used_fact_ids`, the dispute would go out unmarked.
HIS-23 decided:

- **Code finds the disputed facts a draft uses.** `used_fact_ids` is what the model reported (after
  the usual normalisation, unknown ids dropped) plus the disputed facts found by code. A disputed
  fact is found when it has numbers and all of them are in the numbers of the draft's `text` parts
  (no numbering prefixes). The prompt also asks the model to list the disputed facts it mentions;
  that lowers the load on the code check and is the only way for a fact without numbers to count.
- **A dispute is completed whole.** If a reported or found fact belongs to a `Dispute`, the other
  members of the group that went into the prompt are added. Overlapping groups are one unit, the
  same rule as in the short selection. A disputed fact with no group stands alone.
- **Order.** The model's ids keep the model's order, as before, so the behaviour for facts that
  are not disputed is unchanged. The ids code adds follow after them in `FactSet` order.
- **Short posts: the check runs against the selection, not the whole `FactSet`.** A fact the model
  never saw cannot be a fact the post states. If the whole set were checked, a short post that
  happens to contain "60 000" would mark a dispute the prompt did not contain, and the number
  could only have come from the model's own knowledge. For `long` and `thread` the selection is
  the whole set. An id the model itself reports for a fact outside the selection is still kept:
  that is the older behaviour, pinned by a test, and it is not new code's decision.
- **Known limits, accepted:**
  - A disputed fact without numbers is used only if the model names it, or through its group if a
    numbered member of the group is used. A dispute stated in words only (`расходятся`) is not
    detected.
  - The comparison is by number, so a number written in words or as a Roman numeral is not seen
    (the limits of "Matching numbers and dates" apply). Approximate wording does not matter.
  - A disputed fact whose numbers are all shared with other facts (for example only a year) is
    found whenever the text states that year. This errs towards an extra "СПОРНО" mark, which is
    the safe side.
  - After a tail drop, the model's ids still describe the full text (see "Short post length"); the
    disputed facts added by code describe the kept text.

### Short post length

Measured in HIS-21: a short post broke `SHORT_MAX_CHARS` (280) after its one retry in 5 of 8
samples, a retry once grew the text to 538 characters, and the first attempt never fit. The reference
examples did not help (319, 229, 469, 343 characters). The model got every fact of the set, 8 to
10, and used 6 to 8 of them; "aim well below the limit" did nothing, since a model cannot count
characters.

HIS-22 chose two code mechanisms and one prompt change:

- **Fact selection before the call** is the main lever: `SHORT_MAX_FACTS` (3) facts, picked by the
  rules in [pipeline.md](pipeline.md), step 4. Three facts retold take 110 to 270 characters.
- **A retry that names what to cut**: the length, the excess and the quoted longest sentences,
  up to `SHORT_LENGTH_RETRIES` (2) times.
- **A sentence budget** in the format rule replaces "aim well below the limit".

**Tail drop is off by default** (`SHORT_DROP_TAIL=false`), the owner's decision. Dropping the
tail loses information, makes `used_fact_ids` inexact (it still lists facts that were in the
dropped part) and can cut off the second half of a dispute. The mechanism stays behind the flag.
Even when it is on, it is not applied to a post that contains a number of any disputed fact; such
a post stays whole with `length_violations`. The check is by number only: a dispute stated without
digits is not detected, and a number that a disputed fact shares with another fact also blocks the
drop. The second error is the safe one.

Measured with `tests/test_generator_live.py`, DeepSeek, default settings, the Kulikovo and Apollo
11 fact sets:

- No examples, 3 samples per topic: all 6 fit. Lengths 178, 169, 109 (Kulikovo) and 238, 231, 189
  (Apollo 11). 5 fit at the first attempt, 1 after one retry; no second retry, no drop.
- With the reference examples, 2 samples per topic: all 4 fit at the first attempt. Lengths 179, 164
  and 259, 273.
- Threads, 2 samples per topic with and without examples, were unchanged in behaviour and all fit.

Cost of the fix: the selection takes the first facts, which for Kulikovo are the date, the place
and the commander. The posts fit but read like an encyclopedia entry: the duel of Peresvet, the
nickname Донской and Ягайло's delay never reach a short post. Six and four samples are noise-level
evidence. Open: whether the selection should favour a hook over the core facts (an LLM choice, or
another selection for "другой заход").

### Style filter

HIS-7 built step 5 (see [pipeline.md](pipeline.md)). The live HIS-21 drafts showed what the number
check cannot see: filler closers that rate the fact before them ("Это решило многое", "Эта деталь
держит внимание даже спустя столетия"), conclusions no fact states ("Победа на Дону не отменила ни
ордынской силы"), added qualifiers and precisions ("По преданию", "Место известно точно", "Уже в
1382 году") and a pronoun that made a false claim ("вёл их князь Дмитрий" about both armies). The
critic reads the post against the facts for exactly these.

Measured with `tests/test_style_live.py`, DeepSeek, the Kulikovo and Apollo 11 fact sets of the
generator live test:

- Known defects in the saved HIS-21 drafts and two filler closers written into a synthetic thread:
  13 of 14 found in one run. Missed: one "Я не берусь выбирать между этими цифрами" (the same phrase
  in another draft was found).
- Questionable findings on the same bad drafts: "Победа дала Дмитрию прозвище Донской" flagged as
  an added cause (the fact says "после победы"), and "Он"/"Тот не был Чингизидом" right after
  Mamai flagged as ambiguous. Strict but arguable.
- Relatively clean drafts (12 drafts: the HIS-21 dash runs of both topics and the Apollo 11 runs
  without examples): no findings.
- Fresh Kulikovo drafts, 2 threads and 2 short posts: all clean at the first check, no
  regeneration. Started from four known bad drafts, three threads were clean after one
  regeneration. A pre-HIS-22 short draft, which states facts outside today's short selection,
  lost those sentences in the regeneration (the writer is given only the selection) and kept one
  ambiguous-pronoun finding after 2 regenerations.

One run per cell is noise-level evidence.

Known gaps, accepted:

- Stem matching misses forms whose stem changes, and a word's stem can catch an unrelated word
  next to the other words of a phrase. The critic catches other wordings of a stock frame.
- A critic finding can be wrong; it still regenerates. Its excerpt must exist in the post, which
  stops invented findings, not wrong judgements. One finding may be reported under two rules
  (`unsupported_claim` and `filler` for one sentence) and then counts twice.
- Closed in HIS-30: a regeneration that drops content to fix a problem is now rejected (see "Long
  post size and the regression guard"). What stays open: the guard measures characters and the
  facts the model reports, not meaning, so a version that keeps its size but swaps the facts it
  states is not seen.
- A closing question inside a closing quote is not flagged; a final `?` of a quoted question
  without a closing quote is.
- `EMOJI_RANGES` is a list of blocks, not the full emoji definition; dingbats such as ✓ count as
  emoji.
- Hook, rhythm and thread structure are not checked at all.

### Long post size and the regression guard

The first manual run in Telegram (HIS-30): "лонг: Как выглядел обычный день советского человека в
1930-е" returned one sentence. The first long draft was already tiny (offered 20 facts, used 8,
124 output tokens: the format had no minimum and said "as long as the facts deserve"). The critic
found problems, the regeneration returned 38 tokens with 1 fact, the critic found nothing in it,
and it won the choice of the best version with 0 violations. The filter compared the number of
violations, not what was left of the text.

Decided:

- **A floor for `long`:** `LONG_MIN_CHARS` 1200 and `LONG_MIN_USED_FACTS` 6. A paragraph in
  Russian is 300 to 450 characters, so 1200 is about 3 paragraphs: "several paragraphs, not pages".
  6 used facts is 30% of a typical 20-fact set. The facts minimum is capped by the facts that can
  be stated, so a small set is not asked for the impossible. `LONG_MAX_CHARS` (25000) stays a
  ceiling. The prompt says "usually 1200 to 2400" (`LONG_TYPICAL_SIZE_MULTIPLIER` 2) so the model
  does not land exactly on the floor.
- **Under the floor is a length problem:** the existing length retry (`WRITING_LENGTH_RETRIES`, 1),
  a correction with the size, the minimum and the ids of the unused facts, then `Draft` with
  `LengthIssue.TOO_SHORT` and/or `TOO_FEW_FACTS`. Two kinds, because characters and facts are
  different units and the bot has to name which is missing. The violation is report-only in the
  style loop, like every length violation.
- **A regression guard for every format:** a regeneration that keeps less than 0.6 of the
  characters or of the used facts of the last accepted version is rejected, with an allowance for
  a short post (up to 100 characters and 1 fact may go). Without the allowance a short post that
  loses one flagged sentence of 80 characters (2 facts to 1 is a ratio of 0.5) would be rejected
  as a regression. The values 0.6 and the allowance were chosen by the implementer, not measured.
- **Known limits:** the fact count relies on the ids the model reports (code adds only the
  disputed facts it can find by numbers); a version that keeps its size and swaps facts passes; a
  rejected regression spends one regeneration of the budget, so with the default 2 a long post
  gets at most one more try after it.

Live check, DeepSeek, long format, two hand-made 18-fact sets (not from sources), 3 samples each,
the whole write and style loop, 18 requests: all 6 posts were 1330 to 1719 characters and used 16
to 18 facts. The minimum retry and the regression guard were not triggered by a single sample, so
the live run shows only that the new prompt does not collapse; the two mechanisms are covered by
unit tests. The posts read as the facts retold one after another, grouped into paragraphs, not as
a narrative.

### Thread minimum size (HIS-40)

Live runs of HIS-27: the thread on the Kulikovo battle was 2 tweets written from 4 facts, while the
writer was offered 24. The bot already refused a thread below `THREAD_MIN_FACTS` (5) assertable
facts, but nobody checked the result, and the thread format had no floor (`long` got one in HIS-30).

Decided:

- **A floor for `thread`:** `THREAD_MIN_TWEETS` 4 (a hook, two steps, an ending: two or three
  tweets are a short post cut in pieces) and `THREAD_MIN_USED_FACTS` 5. 5 is `THREAD_MIN_FACTS`:
  if the bot lets a thread through on 5 facts, the thread has to use them. 5 facts in 4 tweets is
  one or two facts per tweet, so the floor does not push the model to "one fact per tweet" (style
  rule 16); the prompt says to keep related facts together by meaning. `THREAD_MAX_TWEETS` (12)
  stays a ceiling and the prompt says it is not a target.
- **Settings validation fails the start:** the tweet minimum above `THREAD_MAX_TWEETS`, or the fact
  minimum above `THREAD_MIN_FACTS`, is a configuration error (the owner chose an error over a
  silent adjustment). 0 turns a minimum off. The existing constant `THREAD_MIN_TWEETS` (2) is the
  structural floor of the schema and was not renamed; the default is `THREAD_DEFAULT_MIN_TWEETS`.
- **The effective minimum is capped by the facts on offer:** facts = `min(setting, assertable)`,
  tweets = `min(setting, facts)` (`min(setting, assertable)` when the fact minimum is off).
  Without the second cap a fact minimum of 0 would switch the tweet minimum off through the cap.
- **Under the floor is a length problem:** the existing retry, a thread-specific correction, then a
  `Draft` with `LengthIssue.TOO_FEW_PARTS` (tweets) and/or the existing `TOO_FEW_FACTS`. The facts
  issue is reused because it means "fewer used facts than the minimum" for either format; the
  bot words it by the format. The correction also carries the other length violations: a thread
  can have a tweet over the limit and too few tweets at once.
- **A guard in the style loop:** the 0.6 ratios of HIS-30 let a thread of 4 tweets and 5 facts fall
  to 3 and 4, so a separate check was added. A version is a regression if it is below the
  effective minimum in tweets or in used facts and below the last accepted version in the same
  measure. The owner decided that a first version already under the minimum is guarded too: a
  smaller version (3 tweets to 2) is rejected, an equal one is accepted, so the first version is
  not punished twice.
- **Known limits:** the fact count relies on the ids the model reports (as for `long`); the
  minimum says nothing about whether the thread reads as a story, only how large it is; a
  rejected regression spends one regeneration of the budget.

Live check (`tests/test_thread_live.py`, DeepSeek, thread format, the saved fact sets
`his8_kulikovo` (26 facts, 20 to state) and `his8_soviet_day_1930s` (24 facts, 21 to state), real
sources, 3 samples each, the whole write and style loop, empty examples). The effective minimum was
4 tweets and 5 facts. 44 calls counted at the client level (26 writer, 18 critic; the HTTP and JSON
retries inside the client are not counted).

| | Kulikovo | Soviet 1930s |
| --- | --- | --- |
| Tweets of the 3 samples | 11, 9, 12 | 12, 12, 10 |
| Used facts | 19, 15, 15 | 24, 22, 24 |
| Facts per tweet | 1.73, 1.67, 1.25 | 2.0, 1.83, 2.4 |
| Length violations left | 0 | 0 |
| Regressions rejected | 0 | 0 |
| Style regenerations | 2, 2, 2 | 2, 2, 2 |

- **No sample came near the floor, and the floor itself was not exercised:** every thread had 9 to
  12 tweets and 15 to 24 facts, against 2 tweets and 4 facts in the HIS-27 run. The minimum retry
  and the thread regression guard were not triggered live (no rejected regression); they are
  covered by unit tests only. A length retry of the first draft happened in 4 of 6 samples; the
  run did not record its cause (the second run below does).
- **The new rule overshoots:** the prompt changed a 2-tweet thread into threads at or near the
  `THREAD_MAX_TWEETS` ceiling, in spite of "the maximum is a ceiling, not a target". Soviet
  samples 1 and 2 are exactly 12 tweets. Not changed in this ticket; if the owner wants
  shorter threads, the minimum and the prompt wording are the two knobs.
- **Reading:** it is not "one fact per tweet, each with a closing line": 1.25 to 2.4 facts per
  tweet, tweets of 66 to 280 characters, several tweets carry two or three facts. But it reads as
  the facts retold one after another, not as a story with a line. The Kulikovo sample runs in the
  order of the events, with a few weak tweets at the end (the first white-stone Kremlin in tweet
  11 is off the topic). The Soviet samples jump between working hours, a congress, architecture,
  housing and leisure with no connection between the tweets.
- **Connectors the facts do not state:** the critic caught some and the loop could not remove them
  within 2 regenerations: «Тот же график привёл к росту брака» (a cause), «Карточную систему
  отменят уже в 1935» (a forward reference with a word added). The Kulikovo sample read in full
  has «и это решило исход битвы» (a consequence no fact states) that the critic did not flag.
  Remaining style violations per sample: 0 to 5, mostly `filler`, `unsupported_claim`, `triplet`.

**Second round: a typical range and the cause of a retry.** Two changes after the first live check.

- **A typical size in the prompt**, like `usually 1200 to 2400` for `long`: "A thread usually has
  from {min tweets} to {typical max} tweets", where the typical maximum is the effective tweet
  minimum times `THREAD_TYPICAL_SIZE_MULTIPLIER` (2; 4 to 8 by default), never above
  `THREAD_MAX_TWEETS`. The ceiling stays a hard limit and "a ceiling, not a target". The sentence
  is left out when the tweet minimum is off (0) or the typical maximum would equal the minimum
  (a ceiling at the minimum); with both minimums off the old open rule is unchanged.
- **The cause of a retry is logged (INFO, kinds and numbers only, never a text).** The length
  retry of `write_draft` writes `length retry format attempt parts used_facts violations`, where
  `violations` is `issue=actual/limit` for a thread-wide issue and `issue#part=actual/limit` for a
  tweet (`part_too_long#2=328/280`, `too_many_parts=13/12`, `too_few_parts=2/4`). The style line
  adds `regeneration_triggers` (the rule counts of the version that was regenerated, one group per
  regeneration, separated by `|`) and `regression_causes` (per rejected regression: `thread_parts`,
  `thread_facts`, `chars` or `facts` with `previous>current`, several joined by `+`; `none` when
  empty).

Live check, same sets, same settings, DeepSeek, 3 samples each, empty examples, no Tavily. 44 calls
counted at the client level (28 writer, 16 critic). The saved report is `data/comparisons/
his40_*.json` (local, not tracked), now with the log lines of each sample.

| | Kulikovo | Soviet 1930s |
| --- | --- | --- |
| Tweets (before: 11, 9, 12 / 12, 12, 10) | 11, 10, 9 | 13, 13, 11 |
| Used facts | 18, 18, 17 | 23, 23, 23 |
| Facts per tweet | 1.64, 1.8, 1.89 | 1.77, 1.77, 2.09 |
| First draft retried for | long tweet (3 of 3) | long tweet + 13 of 12 tweets, long tweet + 13 of 12, none |
| Style regenerations | 2, 0, 2 | 2, 2, 2 |
| Regressions rejected | 0 | 0 |
| Left in the chosen draft | `length` (a 281-character tweet) and `unsupported_claim`; none; `ambiguous_reference` and `unsupported_claim` | `length` (13 of 12) and 2 `unsupported_claim`; `length` (13 of 12); `filler` and 3 `unsupported_claim` |

- **The typical range did not work.** 0 of 6 threads are in 4 to 8 tweets (9 to 13), as before.
  Nothing sits exactly on the ceiling of 12 any more, but two threads are one over it (13 of 12:
  the retry could not bring them down and the draft goes out with `too_many_parts`), so the
  ceiling is crossed, not respected. The prompt was checked: the sentence is in it ("usually from 4
  to 8 tweets"), the model does not follow it.
- **The retry was never about the minimum.** 5 of 6 first drafts were retried: all 5 for a tweet
  over 280 characters (from 281 to 423), 2 of them also for 13 tweets of 12. The 7 retries inside
  the style regenerations were the same two kinds (5 for a long tweet, 2 for 15 and 13 tweets). Not once too few tweets or
  too few facts. So the model packs two or three facts into a tweet, overflows 280, and on the
  retry splits the tweet, which adds tweets; on 13 of 12 it merges, and it overflows again. A
  version with 11 tweets averages 170 characters a tweet, far from the 280.
- **Why it stays large:** the model uses nearly all the facts on offer (17 or 18 ids of the 20 to
  state for Kulikovo, 23 of 21 for Soviet; the count includes the disputed facts a draft mentions), not the 5 asked as a floor.
  At about 1.8 facts a tweet that makes 10 to 13 tweets. The tweet minimum and the typical range
  limit it from below and by a hint; nothing limits the number of facts used. The first run said
  that "shorter threads" depend on the minimum and the wording; the second run shows they do not.
- **Reading:** unchanged. The Kulikovo thread reads as a list of facts in a loose order: the
  disputed arrival and the foggy morning (tweets 8 and 9) come after the battle (tweets 6 and 7),
  the last tweet is about the Kremlin. The Soviet thread is a catalogue that jumps from working
  hours to a congress of 1929, a decree of 1940, architecture, housing and the cinema; its last
  tweet is the 30 February remark. The facts were not lost (17 to 23 used), the thread is not a
  story.
- **Options, none applied (the owner decides):** (1) cap the number of facts a thread is offered,
  as `short` does (`select_short_facts`), for instance the first 10 to 12 by relevance, so a
  thread is 5 to 8 tweets by construction; this attacks the cause. (2) A typical number of facts
  in the prompt, "usually 8 to 12 of the facts", beside the typical tweets; cheap, but the same
  kind of hint the model just ignored. (3) Lower `THREAD_MAX_TWEETS` to 8 or 10; it would only
  turn more drafts into `too_many_parts` retries and flagged drafts, since the model does not aim
  below the ceiling. (4) Accept 9 to 13 for a 24-fact set and move the minimum guard only.
  Option 1 is the one that removes the cause; it changes the offered facts of a thread and needs
  its own ticket.
- **Found outside the task:** the style loop can choose a draft that crosses the ceiling or a tweet
  limit by one (13 of 12, 281 of 280), because `length` is a report-only rule and a chosen draft
  with `length_violations` goes out with a warning. Not changed here.

### Thread fact cap (HIS-42)

Option 1 of the HIS-40 follow-up: a thread is offered a bounded set of facts, chosen by code, the
way HIS-22 did for `short`. The writer used nearly every fact it was offered (17 to 23 of 20 to 24)
at about 1.8 facts a tweet, so the number of tweets followed the number of facts, and a hint in the
prompt did nothing. Rules in [pipeline.md](pipeline.md), step 4.

Decided:

- **`THREAD_MAX_FACTS` = 10 asserted facts.** The tweet range is 4 to 8 (`THREAD_MIN_TWEETS` 4
  times the multiplier 2). At the 1.5 to 1.8 facts a tweet measured in HIS-40, 10 facts are 5 to 7
  tweets, inside the range and far from the ceiling of 12. 10 is twice `THREAD_MIN_USED_FACTS`
  (5), so the minimum stays reachable, and above `THREAD_MIN_FACTS` (5), so a set that passes the
  bot gate is cut only when it has more than 10 facts to state. 0 means no cap.
- **`THREAD_MAX_ATTRIBUTED` = 2 units, outside the cap.** Without an allowance a capped thread
  would lose every dispute and every legend, and the "по преданию" and "источники расходятся"
  wordings would never appear. A unit is a disputed group (whole), a claimed fact, or a rebutted
  fact with its rebuttal; the pair is never split, and a rebuttal that is pulled in does not count
  against the cap. Units go in `FactSet` order of their first member. 0 turns them off.
- **The order is the `FactSet` order**, already set by trust tier and relevance (HIS-27, HIS-28).
  No re-sort (`short` sorts `confirmed` before `single`; a thread does not, because step 3 has
  ranked them and a re-sort would undo the relevance and aspect order).
- **Relation to the other thread settings.** `THREAD_MIN_FACTS` is the bot gate on the whole
  `FactSet`; `THREAD_MIN_USED_FACTS` the floor on the used facts; `THREAD_MAX_FACTS` the ceiling
  on the offered ones. `THREAD_MAX_FACTS`, when not 0, must be at least `THREAD_MIN_FACTS`, or the
  gate would let a thread through that the selection cuts below the gate; and so at least
  `THREAD_MIN_USED_FACTS`. The start fails and names both variables; `WritingLimits` checks the
  same. The effective minimum of HIS-40 is capped by the assertable facts of the selection, not of
  the `FactSet`, and the tweet minimum still never exceeds the fact minimum.
- **A thread verifies against the selection:** reported ids, found disputed and attributed facts
  and the number check. An id or a number of a fact that was not offered is unknown or unverified.
  `short` is unchanged and still checks numbers against the whole `FactSet`.
- **The style loop is safe against a changing fact set only because the selections are
  deterministic.** `review_style` and every regeneration call `write_draft` with the whole
  `FactSet`, and `write_draft` selects again; the same input gives the same set, so the regenerated
  draft is written from the facts of the first one, and the regression guard compares like with
  like. If a selection ever becomes non-deterministic (for example an LLM-driven hook or outline
  selection), the style loop must receive the selected set explicitly instead of recomputing it.
  Covered by `tests/test_thread_selection.py`.
- **Known limits:** the selection is by position, not by what makes a story; a dispute that sits
  after the allowance is not offered (a `FactSet` where two legends come before the dispute loses
  the dispute); a rebutted fact whose rebuttal is not assertable is not offered; the typical range
  in the prompt (4 to 8) does not shrink with a small selection; THREAD_MAX_FACTS says nothing
  about the order of the tweets.

Live check (`tests/test_thread_live.py`, DeepSeek, thread, the saved fact sets `his8_kulikovo`
(26 facts, 20 to state) and `his8_soviet_day_1930s` (24 facts, 21 to state), 3 samples each, the
whole write and style loop, empty examples, no Tavily). The saved report is `data/comparisons/
his42_*.json` (local, not tracked). 35 calls counted at the client level (19 writer, 16 critic).
Offered: Kulikovo 12 facts (10 asserted and 2 claimed, F21 and F22; the dispute F24 to F26 came
third in `FactSet` order and was left out), Soviet 13 facts (10 asserted, F21 pulled in as the
rebuttal of F22, F22 rebutted, F23 claimed: 11 assertable and 2 attributed units).

| | Kulikovo | Soviet 1930s |
| --- | --- | --- |
| Tweets (HIS-40: 11, 10, 9 / 13, 13, 11) | 7, 7, 6 | 11, 10, 8 |
| Used facts (HIS-40: 18, 18, 17 / 23, 23, 23) | 9, 9, 8 of 12 | 13, 11, 12 of 13 |
| Facts per tweet | 1.29, 1.29, 1.33 | 1.18, 1.1, 1.5 |
| Length retry of the first draft | none | a 336-character tweet; none; two tweets of 286 and 329 |
| Style regenerations | 2, 2, 2 | 2, 0, 2 |
| Regressions rejected | 0 | 0 |
| Over the tweet ceiling | none | none |
| Left in the chosen draft | `unsupported_claim`; `ambiguous_reference`, `unsupported_claim`, `filler`; none | 3 `unsupported_claim`; none; `length` (a tweet of 291), `cliche`, 2 `unsupported_claim` |

- **Tweets:** all three Kulikovo threads are in the 4 to 8 range (6 to 7), where HIS-40 had 0 of 6.
  Soviet: one of three (8), the others 10 and 11. No thread crosses the ceiling of 12 (HIS-40: two
  at 13 of 12). The cause of the Soviet overshoot is the same as before: the writer used 11 to 13
  of the 13 facts it was offered, at about one fact a tweet. 13 facts are 10 or 11 short tweets
  (51 to 263 characters). A cap of 10 is right for Kulikovo and too high for the Soviet set; the
  attributed units add 2 facts on top of it. Not changed: the value is the owner's decision.
- **Length retries:** only tweets over 280 characters (Kulikovo: no retry; Soviet: 2 of 3 first
  drafts), never the tweet count and never the minimum. One chosen Soviet draft goes out
  with a tweet of 291 characters and `length_violations`, reported (the style loop chose its
  first attempt: `length` is a report-only rule, see "Found outside the task" in HIS-40).
- **Selection in the loop:** the log line `thread facts total offered offered_assertable` is
  identical in all 3 writer calls of every sample (Kulikovo 26 / 20 / 12 / 10, Soviet 24 / 21 / 13
  / 11): regeneration got the same set.
- **Attributed units:** Kulikovo F21 (the duel, claimed) was used in 3 of 3 threads, always with
  its attribution («По преданию, ...», «По легенде, ...»); F22 (the myth of invincibility, claimed)
  in 0 of 3. Soviet F22 (rebutted) was used in 3 of 3 with its rebuttal («Во многих источниках
  указывалось, ... На деле это предложение было отвергнуто», «Часто пишут, ... На деле ...»);
  F23 (Magnitogorsk, claimed) in 2 of 3 («По преданию, ...», «... называют классическим
  моногородом: по этой версии ...»). Observed: the critic flagged one of those wordings
  (`По преданию, Магнитогорск ...`) as `unsupported_claim`, and another as `cliche`, in the
  chosen drafts. Not investigated here.
- **Reading:** tighter and shorter than in HIS-40, still a list of facts rather than a story.
  Kulikovo runs: the battle and its place, Ягайло, the legend of the duel, the ambush regiment,
  the participants, a closing line about unity of Rus; the participants come after the decisive
  moment, and every sample opens with a long sentence of a date, a place and two commanders. The
  Soviet threads are a catalogue of working hours, the continuous week, a congress of 1929,
  architecture, the 30 February myth, Magnitogorsk and the decree of 1940, with no line. The cap
  does not make a story; it only makes a short list.
- **Lost facts:** Kulikovo: the disputed arrival of Mamai (7 or 8 September), the foggy morning and
  the second claimed fact were not offered, so the thread has no dispute at all; the nickname
  Донской only in one thread. Soviet: 10 of the 21 assertable facts were not offered, none of them
  evaluated for importance (the selection is positional).

### Bot delivery

HIS-8 connected the steps in Telegram (see [pipeline.md](pipeline.md), step 6).

- **Thread threshold.** A thread is 2 to 12 tweets; with the facts minimum of 3 a thread could be
  2 tweets of one or two facts each. 5 assertable facts give 3 or 4 tweets with room for related
  facts to share one. Disputed facts do not count: they can be mentioned only cautiously. This
  closes "fewer facts than a thread needs" from the failure behaviour: a short post instead, with
  a warning, rather than asking the user.
- **Timeout.** 600 seconds by default. The live runs below took 26 to 28 s for a topic and 11 to
  15 s for a button, so 600 s is far above a normal run. It is not lower because a single LLM
  attempt may legitimately take up to `LLM_ATTEMPT_TIMEOUT_SECONDS` (300): a total below that
  would cancel a run in which one call was slow but succeeded.
- **Facts after a button.** Only the facts of the new variant, with their sources and "СПОРНО",
  and a line with the number of the others. The full list is in the first answer and does not
  change; repeating 20 facts on every click buries the post. The owner's decision.
- **Progress message** is deleted after a post and turned into the final message otherwise. The
  owner's decision.
- **Known limits.**
  - A short post is always written from the same 3 facts (`select_short_facts`), so "другой
    заход" of a short post changes the presentation and the first sentence, not the facts.
    HIS-25 is to change the selection.
  - A fact block longer than 4096 characters (a fact text near that size) would not be split
    inside and Telegram would reject it; real facts are one or two sentences.
  - Stored runs live in process memory: a restart loses them and the buttons answer "устарели".
  - A button keeps working on any earlier variant while its run is stored; the drafts of one run
    are capped at 30, the oldest dropped first.
  - The timeout counts only the pipeline, not sending the messages.

### Live runs of the whole pipeline (HIS-8)

`tests/test_pipeline_live.py`, DeepSeek on every step, Tavily `basic`, both Wikipedias, the reference
examples, default settings. Per topic: one topic run (short by default), then the "в тред" button
on the stored facts. One run per topic, noise-level evidence.

| Measure                              | "Обычный день советского человека в 1930-е" | "Куликовская битва" |
| ------------------------------------ | ------------------------------------------- | ------------------- |
| Snippets (ru wiki / en wiki / Tavily) | 7 / 4 / 22, 78 791 characters              | 5 / 2 / 18, 55 395  |
| Cut by the input budget (60 000)     | 11 snippets                                 | none                |
| Candidates, over the limit of 40     | 49, 9 dropped                               | 38, none            |
| Support proposed, dropped            | 41; 1 too short, 4 not found, 3 facts dropped on numbers | 46; 1 not found |
| Facts verified, kept, cut by limit   | 32, 20, 12                                  | 37, 20, 17          |
| confirmed / single / disputed        | 1 / 19 / 0                                  | 4 / 16 / 0          |
| Contradiction groups withdrawn       | 7                                           | 10                  |
| LLM calls (topic + button)           | 5 + 6                                        | 8 + 6               |
| Largest extraction output            | 4 329 tokens of 8 000, no truncation        | 3 927, none         |

- `confirmed` worked on live data: the date of the battle stands on wikipedia.org, life.ru,
  prlib.ru and znanierussia.ru. It also confirmed "Мамай заключил союз с Ягайло" from kp.ru and
  youtube.com, and a Soviet fact from wikipedia.org and a LiveJournal blog: the "trusted domains"
  question below is real.
- Every support item the quote check dropped was on a Tavily chunk (Soviet topic: 5 of 44
  Tavily items over all 49 candidates of the reply; Kulikovo: 1 of 40); Wikipedia's were all
  verified. The Soviet run also dropped 3 facts whose numbers were not in their quotes.
- No dispute survived the verdict in either topic, so "СПОРНО" did not appear live; it is covered
  by the tests only.
- English Wikipedia contributed no support of its own in either topic.
- Text quality: posts and threads were clean for the style filter after at most 2 regenerations,
  but read as retold notes. The Soviet short post put 1935 before 1931 and joined them with "Но";
  the Soviet thread is a list of ration norms of the Murmansk archive. The Kulikovo thread follows
  the order of events and reads best.

### Weak domains and the domain cap (HIS-28)

- **Why.** Live runs of HIS-8 gave `confirmed` from pairs such as kp.ru and youtube.com, or
  Wikipedia and a LiveJournal blog, and 12 of 20 facts from one archive page. A manual `long` run on
  the Soviet daily life topic put 13 of 19 used facts from youtube.com, infourok.ru and
  slider-ai.ru into the post, with an error and an added flourish built on them.
- **Default weak list (18).** Video: youtube.com, youtu.be, rutube.ru. Blog hosting: livejournal.com,
  blogspot.com, wordpress.com, medium.com. Social networks, aggregators and Q&A: vk.com, ok.ru,
  dzen.ru, pikabu.ru, reddit.com, quora.com, otvet.mail.ru. School presentations: infourok.ru,
  nsportal.ru, multiurok.ru. AI slide generator: slider-ai.ru. Left out on purpose: sites whose
  quality varies and for which there was no live data (educational aggregators, essay sites).
- **The names.** `FACTS_WEAK_DOMAINS` and `FACTS_MAX_PER_DOMAIN` carry the `FACTS_` prefix because
  they act at fact extraction; `RESEARCH_*` acts before it.
- **The bot.** A weak link is marked "· слабый". A warning is shown when more than half of the facts
  used in the post (`WEAK_USED_WARNING_RATIO`) stand on weak sources only: "N из M", no domains,
  because the facts message already lists them.
- **Live check (two topics, short, one research and one extraction each, the second column
  recomputed from the same model replies).**

  | | Soviet daily life 1930s, before / after | Kulikovo, before / after |
  | --- | --- | --- |
  | Snippets, of them weak | 35, 3 | 26, 2 |
  | Verified facts, weak only | 33, 16 | 40, 0 |
  | Facts shown (20 and disputed) | 22 / 22 | 24 / 24 |
  | `confirmed` | 0 / 0 | 10 / 9 (one lost to a weak pair) |
  | Weak-only facts shown | 16 (73%) / 5 (23%) | 0 / 0 |
  | Domains with a fact | 3 / 6 | 5 / 9 (Wikipedia 17 / 11 of 24) |
  | Cut by the cap, restored by the floor | 3, 0 | 9, 0 |

  The cap never needed the floor, no run was `InsufficientFacts`, and a thread was still offered
  in both. Soviet: the 9 infourok.ru facts and 2 of 5 youtube.com facts left the list, replaced
  by Wikipedia, tass.ru and babel.ua facts; the 3 that stayed fill the places non-weak facts could
  not (there were not enough of them). Kulikovo: 6 Wikipedia background facts (the route to
  Kolomna, Sergius of Radonezh, embassies of 1374-1376) gave way to 7 facts of the course of the
  battle from tass.ru, livingheritage.ru, iz.ru and kp.ru, all `single`. The one `confirmed` fact
  that became `single` (tatarica.org and a youtube.com page) then fell below the limit. The weak
  warning did not fire in either run; it is covered by the tests only.
- **Not decided here.** Weak facts still count as assertable and towards the minimums. A post can be
  written from weak facts only; the mark and the warning are the only signal.

### Stance of a claim (HIS-32)

Found in a manual long run: «В СССР в 1930 и 1931 годах существовало 30 февраля» reached the post as
a fact, and again in a thread after HIS-28. The source gives it as what many sources claim and then
rebuts it; the quote check passed because the quote was verbatim. The same class: legends («по
преданию»), versions («по мнению некоторых историков»), what others claim. The rules are in
[pipeline.md](pipeline.md), step 3, "Stance".

- **The owner's decisions:** a field and not text only; lowering to `asserted` without a marker,
  with a counter; `rebutted_by` instead of a `Dispute`; the labels "версия" and "опровергнуто".
- **Decided by the implementer:** an unknown stance drops the fact; the context is the quote's
  sentence and one neighbour on each side, inside one Tavily chunk and one line; a verified rebuttal
  makes its claim `rebutted` whatever stance the model wrote; a rebutted claim stays out of the
  contradiction pass; attributed claims are kept beyond the limit with no cap of their own, and a
  rebuttal cut by the limit comes back with its claim; the bot warns when a post uses one.

Live check, DeepSeek, Wikipedia ru and en, Tavily `basic`. "Before" ran on the code before any
change, then the research was reused, so "after" spent no Tavily credits. One extraction per case,
noise-level evidence.

| | 30 февраля (one query) | Soviet daily life 1930s | Kulikovo, run 1 / run 2 |
| --- | --- | --- | --- |
| Snippets | 6 | 34 | 27 |
| Before: the myth | a plain fact, `disputed` with «30 февраля в нём не было»; a second fact asserted with «якобы» in its text | n/a | duel and «Сказание» facts plain, `disputed` |
| After: `claimed` / `rebutted` | 3 / 0 | 0 / 0 | 0 / 0, then 2 / 1 |
| After: lowered, unknown | 0, 0 | 1, 0 | 0, 0, then 1, 0 |
| Extraction output tokens (of 8000) | 4209 before, 2317 after | 4030 before, **7987 after** | 5179 before, 5064, then **6370** |

- **30 февраля.** After: «По распространённому утверждению, в СССР в 1930 и 1931 годах якобы
  существовало 30 февраля» is `claimed` (marker «якобы» in its own sentence), so it is in the
  attributed block and not among the facts to state. The model did not use the nested rebuttal: the
  coinciding calendars and «30 февраля в нём не было» came as separate asserted facts, not linked. The
  error of the manual run (the myth as an asserted fact) did not reproduce "before" either: the myth
  was extracted as a plain fact but the contradiction pass made it `disputed`.
- **Attributed facts and their markers, after.** 30 февраля: the myth («якобы»); «Некоторые издания
  считают ... в 3328 году ... 367 дней» («считается»); «Реформу не удалось осуществить, утверждает
  Асташкин» («утверждается», a historian quoted by tass.ru, borderline: an expert opinion rather
  than a version). Kulikovo run 2: «Согласно «Сказанию о Мамаевом побоище» ... победа осталась за
  Пересветом» («сказание»); «Главной причиной похода ... летописи называют ...» («летопись»); «Однако
  судя по тексту «Задонщины» ... Пересвет был жив» `rebutted`, with the «Сказание» version as its
  rebuttal: the roles are swapped.
- **Lowered to `asserted`:** Stakhanov's «31 августа» record (marked `claimed`, the source states it
  plainly: a correct lowering); «по оценкам разных свидетелей, около 80 тысяч войска» (an attribution
  the marker list does not hold, a wrong lowering).
- **False positives** (a fact the source states plainly that became `claimed`): none in the four
  extractions. The Astashkin line is the closest.
- **Not achieved: the duel of Peresvet and Chelubey stayed `asserted`, and `confirmed`, in both
  Kulikovo runs.** A Wikipedia page attributes it to «Сказание о Мамаевом побоище» in the same
  sentence as the quote, imdvor.ru states it plainly, and the model merged the two into one asserted
  fact against rule 8 of the prompt. The run 1 thread states the duel as fact.
- **Output tokens.** The stance fields are about 5% of a reply and the rebuttals about 1% (measured
  on Kulikovo run 2: 856 and 213 of 17 940 characters). The Soviet reply hit 99.8% of the limit
  because the model returned 87 facts against the 40 asked for (38 before), not because of the
  fields. Kulikovo run 2 is 80% with 43 facts.

#### Round 2: the stance of each support item, the roles, the token budget

The owner accepted round 1 in part and asked for three fixes in the same branch, by code where
possible.

- **Strong and weak markers.** Strong: what names a legend, a tradition, a myth or a rumour, and the
  words that disown a claim («предание», «по преданию», «согласно преданию/легенде/сказанию»,
  «легенда», «сказание», «миф», «якобы», «будто бы», «по слухам»; «legend has it», «according to
  legend/tradition», «tradition has it», «allegedly», «supposedly», «it is said», «is said to»,
  «apocryphal», whole «legend» and «myth»). Weak: a reported view or a named source, which is
  normal for a sound historical fact: «считается», «утверждается», «по мнению», «по одной из версий»,
  «многие источники», «указывается», «летопись», «хроника», «согласно житию», «according to»,
  «reportedly». Chronicles are weak on the owner's instruction: a fact «согласно летописи» is an
  ordinary sourced fact. Left out on purpose: bare «tradition» and «legendary» (an «Orthodox
  tradition», a «legendary general» are plain facts), and the adjectives «легендарный» and
  «мифический»; the forms of «легенда» and «миф» match whole for this reason. «по оценкам» is in
  neither list (the owner's decision): estimates with their attribution stay `asserted`.
- **The raise by code.** For every support item of an asserted fact, a strong marker in the quote's
  sentence or a neighbour makes the fact `claimed` and `single`. Weak markers never raise. Not
  raised: a support item whose own sentence opens with a rebuttal opener and has no strong marker,
  because that is the rebuttal next to a legend. The rebuttal of a rebutted fact is checked too, so
  a swapped pair ends with the real claim `claimed`.
- **The text is not rewritten.** A raised fact can read as a plain statement. The attribution in
  the post depends on the attributed block of the writer and on the critic. Rewriting a fact text in
  code would produce Russian no model checked, and the number check of step 3 already ran on it.
- **Swapped roles.** A `rebutted` fact whose quotes all sit in sentences opening with «однако», «на
  самом деле», «в действительности», «however», «in fact» or «actually», with no strong marker in
  them, becomes `asserted` and loses its link. "All" rather than "any", so a real claim is not
  turned into a fact by one odd quote.
- **Token budget.** `stance` is left out for asserted facts, and the prompt states the bound of 40
  candidates at its start and at its end. DeepSeek documents `max_tokens` from 1 to 384K (393 216)
  for `deepseek-flash` ([Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/),
  [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/); read through search
  excerpts, the page itself could not be fetched from this environment), so the limit was raised
  rather than the candidates cut: 7987 x 1.5 = 11 981, rounded to 12000. The cut of candidates by
  code is unchanged. The live calls of round 2 with `max_tokens` 12000 were accepted. The same
  constant applies to Anthropic when the step is switched there; its models allow more output.

Live check, round 2, the recorded snippets of round 1, no Tavily credits, 15 DeepSeek calls (3
extractions of 2 calls, a Kulikovo thread with 6 writer and 3 critic calls). One run per case.

| | 30 февраля | Soviet daily life 1930s | Kulikovo |
| --- | --- | --- | --- |
| Candidates, over the bound | 15, 0 | 40, 2 | 40, 2 |
| Output tokens of 12000 | 1494 (12%) | 4307 (36%) | 6206 (52%) |
| `claimed` / `rebutted` | 1 / 0 | 0 / 0 | 3 / 0 |
| Raised by code, swapped, lowered | 0, 0, 2 | 0, 0, 0 | 2, 1, 0 |

- **The duel is `claimed`**, raised by code: the Wikipedia support has «сказание» in its own sentence
  («... согласно литературному произведению XV века - «Сказанию о Мамаевом побоище», - предшествовал
  Куликовской битве ...»), the imdvor.ru support states it plainly. The other raise: «По «Сказанию о
  Мамаевом побоище» ... победа осталась за Пересветом» (same marker). Swapped: the «Задонщина»
  sentence, opening with «Однако», marked `rebutted` by the model; its rebuttal failed the quote check.
  The thread now says: «По преданию, сражение началось с поединка между русским богатырём Пересветом
  и ордынским воином Челубеем, закончившегося гибелью обоих. По версии «Сказания о Мамаевом
  побоище», победа осталась за Пересветом ...»; the bot warned that F21 and F22 are versions.
- **False positives:** none. No plain fact became `claimed` in the three extractions; both raises
  are the legend. Replaying the recorded round 1 Kulikovo reply through the new code gave the same
  two raises and the swap, and nothing else.
- **30 февраля: a regression of this sample.** The myth from Wikipedia is `claimed` («По некоторым
  источникам, ... якобы существовало 30 февраля»). But the model also wrote a second fact from a
  site that states the myth as its own conclusion («Таким образом, в Советском Союзе в 1930 и 1931
  годах существовало 30 февраля»), marked it `claimed` with «По утверждению источника» in its text,
  and the guard lowered it to `asserted`: the site has no marker. The contradiction pass did not pair
  it with «30 февраля в нём не было». So this run offers the myth to the writer as a fact to state,
  with an attribution in its text. The writer and the critic were not run on this case.

Known gaps, open:

- A myth that a page states as its own claim is `asserted` for that page; only the contradiction pass
  can catch it, and in round 2 it did not. Options: keep `claimed` when the model's text carries an
  attribution and a fact with the same numbers is `claimed` elsewhere in the set, or show the dispute
  pass the claimed facts as well with an instruction to pair them. Not decided.
- The raise errs towards `claimed` for a plain fact next to a legend sentence. None was seen live.
- Swapped roles without an opener («Судя по «Задонщине» ...») are not detected.
- Estimates with their attribution in the text stay `asserted` («по оценкам историков, около 80
  тысяч»).

#### Round 3: a claim against its denial in the contradiction pass

Round 2 left the 30 февраля myth asserted when a page stated it as its own conclusion («Таким
образом, ... существовало 30 февраля») and the contradiction pass did not pair it with «30 февраля
в нём не было». The owner chose the prompt, not a code heuristic: no code links facts by equal
numbers.

- **Decided.** The contradiction prompt says that a claim that something was and a claim that it was
  not (or is refuted) are a contradiction, also when one of them carries an attribution; `[claimed]`
  lines are compared with the plain facts and the denials. Two neutral examples. The verdict, the
  withdrawal, and "a sequence of events and a rounded number are not contradictions" stay. The schema
  is unchanged.
- **Residual risk, accepted.** A myth that a page gives as its own conclusion, with no marker, is not
  caught by code reliably. Only the contradiction pass and then the critic (a disputed fact stated as
  established) can catch it. The decision is not to make the code more complex for it.

Live check, round 3, the recorded snippets, no Tavily credits, 23 DeepSeek calls: 4 extractions of 2
calls and one of 3 (the Soviet reply was asked again once by the client), of which 2 extractions of
30 февраля were spent by two failed runs of the test (the first had no recorded query plan for the
fixed query, the second was the diagnosis); then 7 writer and 5 critic calls for the 30 февраля short
post and thread, no examples.

- **30 февраля.** The page that states the myth as its conclusion became `disputed` together with «30
  февраля в нём не было», explanation: «Один факт прямо утверждает, что 30 февраля в советском
  календаре не было, другой — что в СССР в 1930 и 1931 годах 30 февраля существовало. Это отрицание и
  утверждение существования одной и той же даты.» The Wikipedia myth was `rebutted`, linked to «В
  действительности это предложение было отвергнуто». No fact that states the myth was assertable.
  The thread wrote «В некоторых источниках делают вывод, будто 30 февраля существовало в СССР в 1930 и
  1931 годах» and the next tweet «Советский революционный календарь действительно использовался в
  1930 и 1931 годах, но 30 февраля в нём не было»: the myth and its refutation. The short post did not
  mention it.
- **Cost: false groups on the same set.** 9 groups and 6 withdrawn. Real: the 30-day calendar
  "proposed" against "introduced" (two pages disagree), «30 февраля не было» against "every month had
  30 days", the myth against «не было», "the proposal was rejected" against "introduced".
  Questionable: "proposed" against "Sovnarkom approved the continuous week and a new calendar". False:
  «не было 30 февраля» against the abolition in 1940 (the explanation gives a date the first fact does
  not have), the abolition in 1932 against the final abolition of the continuous week in 1940 (a
  sequence), "introduced" against «реформу не удалось осуществить» twice (a sequence). Two false
  groups reached the thread as «другие источники с этим не согласны» and «По одним данным, в 1932
  году ... По другим, ... в 1940 году. Источники расходятся».
- **Kulikovo:** 2 groups, none withdrawn. The duel: «оба погибли» (plain and «Сказание»), «победа
  осталась за Пересветом» and «Задонщина: Пересвет был жив». Real for «Задонщина» against the death in
  the duel; the «Сказание» facts do not contradict each other, so the group is wider than the
  contradiction. The army of Mamai, 20-30, 70-90 and about 80 thousand: real.
- **Soviet daily life:** no groups, none withdrawn.

Known gaps, open:

- The pass over-reports on a set full of plans, introductions and abolitions (a sequence read as a
  contradiction, an explanation that adds a date). A false group makes the writer say that sources
  disagree where they do not. Seen before HIS-21 and again here; not addressed.
- A group can be wider than the contradiction: facts that agree with each other are pulled in with
  the one that denies them.

### Topic relevance (HIS-27)

Seen in live runs after HIS-8, HIS-28 and HIS-32: the 20 facts of the Soviet daily life topic held
facts about an exhibition and its materials, facts from the 1960s and 1970s, and many facts on one
aspect, and the short post was written from three facts about the exhibition. The rules are in
[pipeline.md](pipeline.md), step 3, "Relevance".

#### Diagnosis

Data: the recorded research of HIS-32 (34 snippets for the Soviet topic, 27 for Kulikovo) and the
three recorded extraction replies of each topic (`his32_before`, `his32_after2`, `his32_after`),
replayed through the code of HIS-32 without API calls. Every verified fact before the cut was
classed by hand: **R** answers the topic, **M** about a source (an exhibition, a researcher, how the
topic is perceived), **P** outside the period, **D** a near repeat of another fact, **G** general
background of the era or another subject.

| | R | M | G | D | P |
| --- | --- | --- | --- | --- | --- |
| Soviet, verified (98 in 3 replies) | 50 | 12 | 29 | 7 | 0 |
| Soviet, kept (60 places) | 33 | 4 | 18 | 5 | 0 |
| Soviet, cut (36) | 16 | 8 | 11 | 1 | 0 |
| Kulikovo, verified (109) | 97 | 4 | 3 | 5 | 0 |
| Kulikovo, cut (30) | 23 | 1 | 3 | 3 | 0 |

- Of the 16 relevant Soviet facts cut, 10 stood on weak sources only (HIS-28 by design) and 6 were
  cut by the model's order alone (hunger and queues, absenteeism, the six-day week, Stakhanov's
  record, temporary housing, «культурность»). Kulikovo lost causes, consequences and the preparation
  of the battle, which the model returned last.
- M by domain: pikabu.ru 8 (how the 1930s are perceived), historyrussia.org 4 (the exhibition, not
  weak, so it reached the writer). D: Stakhanov repeated by baidu.com, istmat.org and archive74.ru,
  so the per-domain cap does not see them. P appeared only in a HIS-32 round 1 run with no recorded
  reply: 5 of 22 facts (1969, the 1960s, 1976 twice, the USA in the 1930s).
- The short post (the first 3 facts): Soviet R G R, **M M M**, R R D; Kulikovo always the date, the
  sides, the names or the outcome.
- Queries were varied. One query of five, «историки о повседневности», brings historiography: in the
  HIS-28 run 9 of 20 kept facts were about a researcher or the study of the topic. Of 11 Wikipedia
  snippets of the Soviet topic 5 were off the topic (Азербайджанцы twice, Алмазная, the Afghan war,
  the Great Depression); they gave no facts.
- **Where relevance is lost:** not in the queries and not in the search but in the order. The
  extraction model returns facts snippet by snippet (round 2 opened with the four exhibition facts)
  although the prompt already asks for the most relevant first; the limit and the short selection
  then take the first ones.

#### Decided

- **One fix: a ranking pass.** An extraction prompt rule was not chosen: the prompt already asks for
  relevance first and is ignored, M is 12% of the facts, and a change to the 12000-token extraction
  could not be compared on recorded replies. A code period filter on its own was not chosen: the
  replays had no P fact and the Kulikovo topic names no year. It became a check of the model's flag
  after the first live run (see the table above).
- **The owner's decisions:** aspects with turns, capped at 8 distinct aspects; turns only among
  relevance 2 and 3; off-topic facts set aside with the floor `max(FACTS_MIN_FACTS,
  THREAD_MIN_FACTS)`; `FACTS_RELEVANCE_ENABLED` on by default; a narrow `about_source`; a fact with no
  claim gets relevance 0; direct causes and consequences are not outside the period.
- **Decided by the implementer:** the pass is on the `fact_extraction` step, after the
  contradictions; disputed and attributed facts are not ranked; a fact with no valid entry counts as
  relevance 2 of the aspect `other`; any `LLMError` falls back to the model's order; the code check of
  `outside_period` with a margin of 10 years; the short selection is unchanged in code and follows the
  new order.

#### Live check

`tests/test_relevance_live.py`, DeepSeek, the recorded research and extraction replies (no Tavily
credits). For each recorded reply: "before" is the replay with the ranking off, "after" the same
replay with one live ranking call. Texts after were written for one reply per topic (Soviet
`his32_after2`, Kulikovo `his32_after`): a short post, "в тред" and a long post. Three runs: the first
showed the wrong `outside_period` flags, the second three wrong `about_source` flags on Kulikovo
(«Епифаний ... сообщает», «По оценкам историков», the memorial day), the third is the code as it
stands. 97 DeepSeek calls over the three runs. Numbers of the third run (the Karamzin fact about the
term is counted as R, by the narrow `about_source`):

| | Soviet, before | Soviet, after | Kulikovo, before | Kulikovo, after |
| --- | --- | --- | --- | --- |
| Facts shown over 3 replies | 62 | 62 | 79 | 79 |
| R / M / G / D | 34 / 4 / 18 / 6 | 41 / 0 / 16 / 5 | 77 / 0 / 0 / 2 | 77 / 0 / 0 / 2 |
| Short posts made of M facts | 1 of 3 (M M M) | 0 | 0 | 0 |
| Set aside: about a source / outside / relevance 0 | | 4 / 0 / 1 | | 0 / 0 / 1 |
| `outside_period` flags dropped by code | | 6 | | 0 |
| `InsufficientFacts` | 0 | 0 | 0 | 0 |

- **Soviet, the exhibition reply:** the four exhibition facts and two background facts left; hunger,
  absenteeism, the six-day week, Stakhanov's record and two housing facts came in. The short post
  went from three exhibition facts to housing, working hours and housing. The latest reply lost two
  of eight Stakhanov facts and the congress, and gained «культурность» and temporary housing.
- **Kulikovo:** the set is about the battle in all replies, so relevance changes little; the turns
  of aspects brought in causes (tribute of 1371 and 1374) and consequences (tribute stopped, the
  throne passed without a yarlyk) in place of the camp on 6 September, Mamai's headquarters and a
  second «turning point» fact. The short post is still the date, the sides and the names: that is a
  question of a hook (HIS-25), not of relevance.
- **Set aside wrongly in the third run:** none of the M facts was a normal fact. One fact with
  relevance 0 is arguable: «Сталин ... провозгласил это движение всенародным» (G by hand). The model is
  not stable: the same input gave three wrong `about_source` flags in the second run and none in the
  third.
- **Not improved:** G facts (the number of workers, hidden unemployment, free education) still fill
  a third of the Soviet list, because the model scores them 1 or 2 and there are not enough R facts
  to replace them. The Kulikovo thread after the ranking was 2 tweets of 4 facts; the writer, not the
  facts, decided that.
- **Cost:** one call per topic on the `fact_extraction` step, none for the buttons. Its time was not
  measured separately.

Known gaps, open:

- `about_source` depends on the model alone; a wrong flag loses a normal fact unless the floor brings
  it back. Counted in `facts_about_source`, never shown by text in the bot.
- A topic that names a century in Roman numerals or an era by name gets no period check, so
  `outside_period` never sets a fact aside for it.
- The planning prompt asks for "how historians assess it", which brings historiography for a topic
  about a way of life. Not changed here.

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

- Timing, measured on the owner's machine (HIS-20): `make test` took 3.2 to 4.1 s wall time over
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

Settled in HIS-28: a configurable list of weak domains (`FACTS_WEAK_DOMAINS`) that do not count
towards `confirmed` and sort lower, a per-domain cap (`FACTS_MAX_PER_DOMAIN`), and the difference
from `RESEARCH_BLOCKED_DOMAINS` (see "Weak sources" in [pipeline.md](pipeline.md), step 3, and
"Weak domains and the domain cap" above).

Still open:

- A positive list of trusted domains, for example whether one trusted source may be enough for
  `confirmed`. Nothing is built for it.
- Two non-weak sites that copy each other (a news aggregator and its source) still count as
  independent. The weak list does not catch them.
- The weak list is a starting guess, and borderline sites (for example an educational aggregator
  that showed up in a live `confirmed` fact) are not on it. Tune it from live runs, in `.env`.

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
    words passes the check unseen. Since HIS-21 style rule 15 asks for digits in dates, years,
    terms, sums, sizes, ages and percentages and forbids computed intervals; small counts in words
    are allowed and stay unchecked. A rule narrows the gap, it does not close it.
  - Approximate wording is ignored: `около 300` and `300` are the same number to the check.
  - `1.500` reads as 1500; a date with dots splits into the day and month and the year.
- Since HIS-7 step 5 turns every unverified number into a violation that regenerates the post.
- Still open: whether Roman numerals and numbers in words get a conversion.

### Merged claims

The model groups support for one claim during extraction (decided above). Open: the model may
merge two different claims into one fact, and two domains behind a merged fact would make it a
false `confirmed`. A cheap embedding or string-overlap check could back the grouping up. Not
decided.

### Images

Pictures for posts are planned through Wikimedia Commons, **later**. Open: how an image is picked,
how its licence and attribution are shown to the user, and whether the bot suggests or attaches
it. The `images` scope is reserved. No code until a ticket for it exists.

### Length limits

Decided as config values (HIS-6), defaults to be revisited after real drafts:

- Short post: `SHORT_MAX_CHARS`, 280.
- Long post: `LONG_MAX_CHARS`, 25000 (X Premium).
- Long post minimum: `LONG_MIN_CHARS` 1200 and `LONG_MIN_USED_FACTS` 6, a floor with one retry
  (HIS-30, see "Long post size and the regression guard").
- Thread: `THREAD_TWEET_MAX_CHARS`, 280 per tweet, and `THREAD_MAX_TWEETS`, 12. The model splits
  at meaning boundaries; a tweet is never cut mid-sentence by code.
- Counting is `len()` of the part as delivered. X counts a URL as 23 characters and emoji and CJK
  as 2, after NFC; for Russian text without links and emoji the counts match.
- On a breach of a `long` post or a thread the model gets one retry with the exact parts and
  numbers. If it still breaks a limit, the draft is delivered with `length_violations`; code never
  truncates these formats.
- A short post: fact selection, a sentence budget, up to 2 retries that quote the sentences to
  cut, and an optional tail drop that is off by default (HIS-22, see "Short post length").
- Numbering (`1/ `) is off by default (`THREAD_NUMBERING`). Code adds it after the reply, the
  model is given a tweet limit reduced by the widest prefix, and the full length is checked.
- A thread needs `THREAD_MIN_FACTS` (5) assertable facts, decided in HIS-8 (see "Bot delivery").
- Thread minimum: `THREAD_MIN_TWEETS` 4 and `THREAD_MIN_USED_FACTS` 5, a floor with one retry
  (HIS-40, see "Thread minimum size").

### Few-shot selection

How examples enter the prompt once they exist: all of them, the N most recent, or the N most
similar to the topic. Also how many fit before the prompt costs more than the style gain.

- Provisional default, implemented in HIS-6: the first `EXAMPLES_MAX` (3) non-empty `*.md` files
  in `EXAMPLES_DIR` (`data/examples`) sorted by name. Hidden files, `.gitkeep`, other extensions
  and files that are not UTF-8 are skipped. No examples means no examples block.
- Open: an example has no length cap, so a long reference post makes every prompt longer.

### Critic strictness

What happens when the critic keeps finding violations after the 2 allowed regenerations.

- Implemented in HIS-7 as the provisional default (in [pipeline.md](pipeline.md), step 5): deliver
  the best version with the unresolved violations listed.
- To settle: whether the owner prefers a hard stop, and whether the dangerous rules should block
  delivery rather than only weigh more.

### Persistence content

What SQLite will hold when it arrives: only the fact and draft history, or also research
snippets and caching of repeated topics. Not needed until the no-database stage proves a gap.
