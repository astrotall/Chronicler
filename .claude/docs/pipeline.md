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
- Code enforces the count (3-5) and drops duplicates and empty queries.

### 2. Research

Input: the queries. Output: a list of `Snippet`.

- No LLM. Every research source implements `search(query) -> list[Snippet]` (see
  [architecture.md](architecture.md)). Sources: Wikipedia ru, Wikipedia en, Tavily.
- Sources run concurrently. A source that fails is logged and skipped; the pipeline continues
  with the others. If every source fails, the run stops with an error to the author.
- Snippets are deduplicated by URL and by text.

### 3. Fact extraction

Input: the snippets. Output: a `FactSet`.

- LLM step: `fact_extraction`. The model returns atomic facts. Each fact has a statement in
  Russian, the id of the snippet it came from, and a **verbatim quote** from that snippet.
- **Code verifies every quote.** The quote must occur in the text of the named snippet, after
  normalising whitespace and quote marks only. A fact whose quote is not found is discarded.
  The model is never asked "is this quote real".
- Code assigns the status of each fact (see below) from the sources behind it.
- The model may mark which facts contradict each other. Code turns that mark into `disputed`.

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

Described as ideas. Exact fields are fixed when the code is written. All of them are Pydantic v2
models and live in `app/domain/`.

| Model        | Idea                                                                                                           |
| ------------ | -------------------------------------------------------------------------------------------------------------- |
| `Snippet`    | A piece of source text as returned by a research source: text, URL, title, source name, language, retrieval time |
| `SourceRef`  | A pointer from a fact to its evidence: the snippet id, the URL, the domain, the verbatim quote                  |
| `Fact`       | One atomic claim in Russian, one or more `SourceRef`, a `FactStatus`, optional links to contradicting facts     |
| `FactStatus` | `confirmed`, `single`, `disputed`                                                                              |
| `FactSet`    | The facts that survived verification for one topic, plus what was dropped and why                               |
| `Draft`      | A post or thread: its parts, the format, the ids of the facts it uses, the attempt number, the violations found |

### Fact status

| Status      | Meaning                                                                                  |
| ----------- | ---------------------------------------------------------------------------------------- |
| `confirmed` | The same claim stands on 2 or more independent domains                                   |
| `single`    | One source, or several sources on the same domain                                        |
| `disputed`  | Sources contradict each other on this claim                                              |

`disputed` takes precedence over the other two. "Independent" means a different registrable
domain. How language editions of Wikipedia count is an open question in
[decisions.md](decisions.md).

## Who checks what

| Check                                                   | Done by    | Why                                                      |
| ------------------------------------------------------- | ---------- | -------------------------------------------------------- |
| LLM reply is valid JSON of the expected shape           | Code       | Pydantic validation                                      |
| Number of search queries                                | Code       | Plain count                                              |
| Quote occurs in the snippet text                        | Code       | Substring match after whitespace and quote normalisation |
| Fact status (domain count)                              | Code       | Counting registrable domains                             |
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
- No fact survives step 3: stop and say so. Do not write a post from the topic alone.
- Fewer facts than a thread needs: say so and offer a short post.
- The provider for a step is unavailable: the error names the step and the provider. There is no
  silent fallback to the other provider, because that would change the cost and the voice
  without the author knowing.
