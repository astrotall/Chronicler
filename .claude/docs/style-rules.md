# Style rules

Rules for the text of every post and thread the bot writes. They are normative: the writing
prompt, the deterministic filter and the critic prompt all derive from this file. When a rule
changes here, the three change in the same commit (see [CONTRIBUTING.md](../../CONTRIBUTING.md)).

Posts are in Russian. This document is in English; the Russian examples are the only exception.

## Voice

1. **Russian, first person.** The post is written as the author, "я".
2. **Opinions and judgements in first person are allowed.** "Мне кажется, это была ошибка." is
   fine, as long as it is a judgement and not a claim of fact.
3. **Invented personal experience is forbidden.** The author did not see, visit or witness
   anything the bot cannot know. Never "я видел", "я был там", "когда я стоял у этих стен",
   "мне довелось". The bot has facts and an opinion, not a biography.

## Punctuation

4. **No long or medium dashes.** Neither the em dash (U+2014) nor the en dash (U+2013). Replace
   with a full stop, a comma or a colon, whichever the sentence needs. A plain hyphen inside a
   word ("кто-то", "юго-запад") is fine.

## Banned phrases

5. These constructions are forbidden, in any inflection or letter case:

   - "это не просто X, а Y" (the whole "not just X, but Y" frame)
   - "давайте разберёмся"
   - "давайте погрузимся"
   - "стоит отметить"
   - "в заключение"
   - "знали ли вы"

   The list lives in config, not in code (see the no magic strings rule in
   [CLAUDE.md](../../CLAUDE.md)). It is expected to grow. Adding a phrase is a config change.

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
    | short  | A short post, the default                                               |
    | long   | A long single post. The limit is in config, 25000 characters by default (X Premium) |
    | thread | A sequence of posts, each within the per-post limit from config         |

    The limits are config values. Code never hard-codes 25000 or the per-post limit.

## Facts and style meet here

The style rules never override the factual rules. A post that sounds good and contains a figure
that is not among the facts is rejected. A post that is accurate and reads like a press release is
regenerated.

For `disputed` facts the cautious wording is part of the style: attribute the claim and say the
sources disagree. Do it in a plain sentence, not with a formula from the banned list.

## Few-shot examples

The author's voice is set mainly by examples, not by rules.

- Reference posts are plain text files in `data/examples/`, one post per file.
- **At the start there are none.** The mechanism must work with an empty set: with no examples
  the writing prompt simply omits the examples block, and nothing fails.
- The number of examples included in a prompt is a config value. Selection (all, the most recent,
  the most similar to the topic) is an open question in [decisions.md](decisions.md).
- Examples are the author's own writing or posts the author approved. They are never generated
  by the bot and fed back in.

## How the rules are enforced

The banned-phrase list is only part of the defence against AI slop. A model learns to avoid a
list and finds the next cliche. The main levers are few-shot examples and the critic pass.

| Rule                                          | Enforced by                          |
| --------------------------------------------- | ------------------------------------ |
| 4 dashes                                      | Deterministic                        |
| 5 banned phrases                              | Deterministic (pattern list in config) |
| 9 emoji, 10 hashtags                          | Deterministic                        |
| 11 closing question                           | Deterministic (a final `?`), critic confirms the intent |
| 12 length                                     | Deterministic                        |
| 3 invented personal experience                | Deterministic for known phrases ("я видел", "я был там"), critic for the rest |
| 6 triplets                                    | Critic, with a rough deterministic hint |
| 7 hook                                        | Critic                               |
| 8 rhythm                                      | Critic, with a deterministic sentence-length spread as a hint |
| 1, 2 voice                                    | Few-shot and critic                  |

A deterministic check is a hard gate. A critic finding is also a gate (it triggers a
regeneration), but the critic is a model and can be wrong, so its verdict is shown to the author
if the attempts run out.

## Examples

Each pair shows a defect and a repair. These are illustrations of the rules, not reference posts
for few-shot.

Dash (rule 4):

- Плохо: `Он выиграл битву — и проиграл войну.`
- Хорошо: `Он выиграл битву и проиграл войну.`

Banned frame (rule 5):

- Плохо: `Это не просто крепость, а символ эпохи.`
- Хорошо: `Крепость строили одиннадцать лет, и за это время сменилось три князя.`

Invented experience (rule 3):

- Плохо: `Я видел эти стены своими глазами, они действительно огромны.`
- Хорошо: `По-моему, эти стены строили для показа. Врага такими не остановишь.`

Hook (rule 7):

- Плохо: `Сегодня поговорим об одной интересной странице истории.`
- Хорошо: `Мост взорвали за четыре минуты до подхода колонны. Диверсии не было: сапёр перепутал время.`

Rhythm (rule 8):

- Плохо: `Король вышел из замка. Он сел на коня. Он поехал к реке. Он не вернулся.`
- Хорошо: `Король выехал из замка на рассвете и поскакал к реке, которую знал с детства. Не вернулся.`
