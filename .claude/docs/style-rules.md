# Style rules

Rules for the text of every post and thread the bot writes. They are normative: the writing
prompt, the deterministic filter and the critic prompt all derive from this file. When a rule
changes here, the three change in the same commit (see [CONTRIBUTING.md](../../CONTRIBUTING.md)).

Posts are in Russian. This document is in English; the Russian examples are the only exception.

## Voice

1. **Russian, about the facts.** When the author speaks of themself it is in the first person,
   "я", but the post is about the facts, not about the author. The rule used to say "write in the
   first person", and the model read it as a request to put "я" into every post.
2. **A first-person opinion is allowed, rarely.** At most `OPINION_MAX_PER_POST` (1) in a post or
   in a whole thread, and a post may well have none. It is about what the facts show and reads as
   a judgement, not a claim of fact: "Мне кажется, это была ошибка." It is never the habitual
   closing line of a part: "Считаю, что прозвище здесь точнее любой летописной похвалы." after a
   fact is the defect this rule exists for.
3. **Invented personal experience is forbidden.** The author did not see, visit or witness
   anything the bot cannot know. Never "я видел", "я был там", "когда я стоял у этих стен",
   "мне довелось". The bot has facts and an opinion, not a biography.

## Punctuation

4. **No long or medium dashes.** Neither the em dash (U+2014) nor the en dash (U+2013). Where
   a dash is needed, write a hyphen with spaces, " - " (`DASH_REPLACEMENT` in
   `app/config/style.py`). The spaced hyphen replaces a dash only: it does not stand in for a
   comma, a full stop or a colon and is not a default punctuation mark. A hyphen inside a word
   ("кто-то", "по-петровски") and in a range ("1320-1330") is fine. Only the characters in
   `FORBIDDEN_DASHES` are a violation; a plain hyphen never is.

## Banned phrases

5. These constructions are forbidden, in any inflection or letter case:

   - "это не просто X, а Y" and "не просто X, а Y" (the whole "not just X, but Y" frame, with or
     without "это": "Он был не просто город, а крепость" is caught, "не просто так" is not)
   - "давайте разберёмся"
   - "давайте погрузимся"
   - "стоит отметить"
   - "в заключение"
   - "знали ли вы"

   The list lives in config, not in code (see the no magic strings rule in
   [CLAUDE.md](../../CLAUDE.md)): `BANNED_PHRASES` in `app/config/style.py`, next to the
   forbidden dashes, the known invented-experience phrases, the cautious wordings for disputed
   facts, the opinion cap and the bad and good examples of rules 2, 4 and 13-15. It is expected
   to grow. Adding a phrase or an example is a change to that module only: the writing prompt
   renders its rules block from it (`app/prompts/style_rules.py`, in Russian), and the style
   filter and the critic read the same data.

   "In any inflection" is a stem match without a morphology library: each word of a phrase loses a
   common Russian ending and matches any ending, short words match whole, `X` and `Y` stand for 1
   to 8 words, and a phrase never spans two sentences. A word in `BANNED_PHRASE_EXACT_WORDS`
   matches whole: "в заключение" is banned, "в заключении мира" is not. The details are in
   [pipeline.md](pipeline.md), step 5.

## Structure

6. **No triplets.** The text is not built on lists of three: three adjectives, three parallel
   clauses, three examples in a row, three paragraphs with the same shape. Two items are fine,
   four are fine, and a single sharp detail is better than either.
7. **The first sentence hooks with something concrete:** a detail, a number or a paradox. Not a
   greeting, not a topic announcement, not a rhetorical question, not "история знает много
   примеров".
8. **Sentence length varies.** A short sentence after a long one. Живая речь, not a metronome.
   Several sentences of the same length in a row are a defect.

## Decoration

9. **No emoji.**
10. **No hashtags.**
11. **No question to the reader at the end,** unless the author explicitly asked for one in the
    request.

## Length

12. A post is one of three shapes:

    | Shape  | Meaning                                                                 |
    | ------ | ----------------------------------------------------------------------- |
    | short  | A short post, the default. The limit is in config, 280 characters by default |
    | long   | A long single post. The limit is in config, 25000 characters by default (X Premium) |
    | thread | A sequence of posts, each within the per-post limit from config         |

    The limits are config values. Code never hard-codes 25000 or the per-post limit.

## Content

These came from the first live drafts (HIS-21): every tweet ended with a line that commented on
the fact before it, and the thread was one fact per tweet.

13. **No filler sentences.** No sentence whose only job is to restate, comment on or rate the
    previous one ("Это деталь, которая держит внимание даже спустя столетия."). If a sentence can
    go and nothing is lost, it goes. A part may end on the fact itself.
14. **No meaning the facts do not state.** No conclusion, cause, consequence or claim of
    importance that is not among the facts ("Союзник не пришёл, и это решило многое."). The post
    may choose facts, put them side by side and tell them vividly; the reader draws the
    conclusion. The number check does not catch this, so it is also stated in the writing rules
    as a "new claim".
15. **Numbers in digits.** Dates, years, terms, sums, sizes, ages and percentages are written in
    digits, as in the facts, so the number check sees them. Small counts, one to ten, may be words
    in ordinary speech ("два войска", "четыре вагона"). An interval, term or count that no fact
    states is never computed: "Через два года, в 1382 году" is forbidden when no fact says "2
    years". Roman-numeral centuries ("XIV век") are left as they are and are not checked.
16. **A thread is not "one fact per tweet, each with a closing line".** A tweet may be one plain
    sentence, and related facts may share a tweet. A tweet is still never a fragment: it says who
    and what it is about.

## Facts and style meet here

The style rules never override the factual rules. A post that sounds good and contains a figure
that is not among the facts is rejected. A post that is accurate and reads like a press release is
regenerated.

For `disputed` facts the cautious wording is part of the style: attribute the claim and say the
sources disagree. Do it in a plain sentence, not with a formula from the banned list.

## Few-shot examples

The author's voice is set mainly by examples, not by rules.

- Reference posts are Markdown files (`*.md`) in `data/examples/`, one post per file. Hidden
  files, `.gitkeep`, other extensions and empty files are ignored.
- **At the start there are none.** The mechanism must work with an empty set: with no examples
  the writing prompt simply omits the examples block, and nothing fails.
- The number of examples included in a prompt is a config value. Selection (all, the most recent,
  the most similar to the topic) is an open question in [decisions.md](decisions.md).
- Examples are the author's own writing or posts the author approved. They are never generated
  by the bot and fed back in.

## How the rules are enforced

The banned-phrase list is only part of the defence against AI slop. A model learns to avoid a
list and finds the next cliche. The main levers are few-shot examples and the critic pass. The
filter is step 5 in [pipeline.md](pipeline.md).

| Rule                                          | Enforced by                                                    |
| --------------------------------------------- | -------------------------------------------------------------- |
| 4 dashes                                      | Deterministic, `FORBIDDEN_DASHES`                              |
| 5 banned phrases                              | Deterministic, stem match of `BANNED_PHRASES`; the critic flags other wordings of a stock frame as `cliche` |
| 9 emoji, 10 hashtags                          | Deterministic                                                  |
| 11 closing question                           | Deterministic: the last part ends with `?`; the caller can allow it |
| 12 length                                     | Deterministic, step 4; reported by the filter, regenerates only together with another violation |
| 3 invented personal experience                | Deterministic for `INVENTED_EXPERIENCE_PHRASES`, critic for other wordings |
| 6 triplets                                    | Critic, rhetorical triplets only; a list from the facts is fine |
| 2 opinion cap and opinion as a closing line   | Critic                                                         |
| 13 filler sentences                           | Critic                                                         |
| 14 meaning beyond the facts                   | Critic, reading the post against the facts: conclusions, causes, claims of importance, added qualifiers ("по преданию") and precisions ("точно", "уже"), and pronouns that change the meaning |
| 15 numbers in digits                          | Deterministic for digits (the number check of step 4, a violation in step 5); a computed interval in words is an unsupported claim for the critic |
| 1 voice, 7 hook, 8 rhythm, 16 thread structure | Not enforced automatically: prompt and few-shot only          |

A deterministic check is a hard gate. A critic finding is also a gate (it triggers a
regeneration), but the critic is a model and can be wrong, so its findings are shown to the
author if the attempts run out, and an excerpt the critic quotes must occur in the post or the
finding is dropped. When the attempts run out, the version with the fewest dangerous violations
(`DANGEROUS_STYLE_RULES`: unsupported claims, unverified numbers, ambiguous pronouns, invented
experience) goes out, then the fewest violations in total.

## Examples

Each pair shows a defect and a repair. These are illustrations of the rules, not reference posts
for few-shot.

Dash (rule 4):

- Плохо: `Победа — это начало.`
- Хорошо: `Победа - это начало.`

Banned frame (rule 5):

- Плохо: `Это не просто крепость, а символ эпохи.`
- Плохо: `Он был не просто город, а крепость.`
- Хорошо: `Крепость строили одиннадцать лет, и за это время сменилось три князя.`

Invented experience (rule 3):

- Плохо: `Я видел эти стены своими глазами, они действительно огромны.`
- Хорошо: `По-моему, эти стены строили для показа. Врага такими не остановишь.`

Hook (rule 7):

- Плохо: `Сегодня поговорим об одной интересной странице истории.`
- Хорошо: `Мост взорвали за четыре минуты до подхода колонны. Диверсии не было: сапёр перепутал время.`

Opinion as a closing line (rule 2):

- Плохо: `После победы Дмитрий получил прозвище Донской. Считаю, что прозвище здесь точнее любой летописной похвалы.`
- Хорошо: `После победы Дмитрий получил прозвище Донской.`

Filler sentence and added meaning (rules 13, 14):

- Плохо: `Мамай ждал Ягайло, но тот не успел к битве. Союзник не пришёл, и это решило многое.`
- Хорошо: `Мамай ждал Ягайло, но тот не успел к битве.`

Numbers (rule 15):

- Плохо: `Через два года, в 1382 году, Тохтамыш сжёг Москву.`
- Хорошо: `В 1382 году Тохтамыш сжёг Москву.`
- Допустимо: `На поле сошлись два войска.`

Rhythm (rule 8):

- Плохо: `Король вышел из замка. Он сел на коня. Он поехал к реке. Он не вернулся.`
- Хорошо: `Король выехал из замка на рассвете и поскакал к реке, которую знал с детства. Не вернулся.`
