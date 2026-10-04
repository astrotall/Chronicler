import pytest
from app.bot import messages
from app.bot.formatting import (
    facts_messages,
    pack_blocks,
    post_ready_messages,
    render_rework_outcome,
    render_topic_outcome,
    split_plain,
    warnings,
)
from app.bot.keyboards import DraftCallback, draft_keyboard
from app.bot.requests import TopicRejection, TopicRequest, parse_topic
from app.config.constants import TELEGRAM_MESSAGE_MAX_CHARS, TOPIC_MAX_CHARS
from app.domain.draft import Draft, DraftPart, LengthIssue, LengthViolation, PostFormat
from app.domain.fact import (
    Dispute,
    ExtractionStats,
    Fact,
    FactSet,
    FactStatus,
    InsufficientFacts,
    SourceRef,
)
from app.domain.pipeline import (
    DraftExpired,
    FailureKind,
    NoSources,
    NotEnoughFacts,
    NothingFound,
    PipelineStage,
    PostAction,
    PostReady,
    ResearchFailed,
    StepFailed,
    ThreadDowngrade,
    ThreadUnavailable,
)
from app.domain.research import SourceFailure
from app.domain.style import (
    CriticStatus,
    StyleReport,
    StyleResult,
    StyleRule,
    Violation,
    ViolationSource,
)

LIMIT = 50
WIKI_URL = "https://ru.wikipedia.org/wiki/Битва"
SITE_URL = "https://history.example.org/page?a=1&b=<2>"
FAILURE = SourceFailure(source="tavily", query="q", kind="SourceUnavailableError", reason="x")


def source(url: str) -> SourceRef:
    return SourceRef(snippet_id="s", url=url, domain="d", quote="цитата")


def make_fact(fact_id: str, text: str, status: FactStatus, *urls: str) -> Fact:
    return Fact(
        id=fact_id, text=text, status=status, support=[source(url) for url in urls or (WIKI_URL,)]
    )


FACT_SET = FactSet(
    topic="Битва",
    facts=[
        make_fact("F1", "Битва была в 1380 году.", FactStatus.CONFIRMED, WIKI_URL, SITE_URL),
        make_fact("F2", "Командовал <князь> & воевода.", FactStatus.SINGLE),
        make_fact("F3", "Было 60 000 воинов.", FactStatus.DISPUTED),
        make_fact("F4", "Было 150 000 воинов.", FactStatus.DISPUTED, SITE_URL),
        make_fact("F5", "Источники спорят о месте.", FactStatus.DISPUTED),
    ],
    disputes=[Dispute(fact_ids=["F3", "F4"], explanation="Оценки <расходятся>.")],
)


def make_ready(
    texts: list[str] | None = None,
    post_format: PostFormat = PostFormat.SHORT,
    *,
    used: list[str] | None = None,
    violations: list[Violation] | None = None,
    critic: CriticStatus = CriticStatus.CHECKED,
    regeneration_failed: bool = False,
    regressions_rejected: int = 0,
    unverified: list[str] | None = None,
    length: list[LengthViolation] | None = None,
    dropped: list[str] | None = None,
    variant: bool = False,
    downgrade: ThreadDowngrade | None = None,
    failures: list[SourceFailure] | None = None,
) -> PostReady:
    draft = Draft(
        post_format=post_format,
        parts=[DraftPart(text=text) for text in texts or ["Текст поста."]],
        used_fact_ids=used if used is not None else ["F1", "F3", "F4"],
        unverified_numbers=unverified or [],
        length_violations=length or [],
        attempts=1,
        dropped_tail=dropped or [],
    )
    return PostReady(
        draft_id="abcdef012345",
        result=StyleResult(
            draft=draft,
            report=StyleReport(violations=violations or [], critic=critic),
            attempts=1,
            chosen_attempt=1,
            regenerations=0,
            regeneration_failed=regeneration_failed,
            regressions_rejected=regressions_rejected,
        ),
        fact_set=FACT_SET,
        actions=list(PostAction),
        variant=variant,
        downgrade=downgrade,
        failures=failures or [],
    )


def test_a_short_text_is_one_message() -> None:
    assert split_plain("Абзац.", LIMIT) == ["Абзац."]


def test_a_long_text_is_split_at_paragraphs() -> None:
    paragraphs = ["Первый абзац текста.", "Второй абзац текста.", "Третий абзац текста."]

    chunks = split_plain("\n\n".join(paragraphs), LIMIT)

    assert chunks == ["Первый абзац текста.\n\nВторой абзац текста.", "Третий абзац текста."]


def test_a_paragraph_over_the_limit_is_split_at_sentences() -> None:
    text = "Короткий абзац.\n\nОдно предложение тут. Другое предложение там. И третье тоже здесь."

    chunks = split_plain(text, LIMIT)

    assert chunks[0] == "Короткий абзац."
    assert all(len(chunk) <= LIMIT for chunk in chunks)
    assert " ".join(chunks[1:]) == (
        "Одно предложение тут. Другое предложение там. И третье тоже здесь."
    )


def test_a_sentence_over_the_limit_is_split_at_spaces() -> None:
    sentence = " ".join(["слово"] * 30)

    chunks = split_plain(sentence, LIMIT)

    assert all(len(chunk) <= LIMIT for chunk in chunks)
    assert " ".join(chunks) == sentence


def test_a_word_over_the_limit_is_cut_hard() -> None:
    chunks = split_plain("я" * (LIMIT * 2 + 1), LIMIT)

    assert [len(chunk) for chunk in chunks] == [LIMIT, LIMIT, 1]


def test_a_long_post_fits_telegram_messages() -> None:
    paragraph = "Предложение длиной примерно в сорок знаков. " * 30
    text = "\n\n".join([paragraph.strip()] * 10)
    ready = make_ready([text], PostFormat.LONG)

    outgoing = post_ready_messages(ready)
    last = next(index for index, m in enumerate(outgoing) if m.keyboard is not None)
    posts = [m.text for m in outgoing[: last + 1]]

    assert len(posts) > 1
    assert all(len(post) <= TELEGRAM_MESSAGE_MAX_CHARS for post in posts)
    assert "\n\n".join(posts) == text


def test_a_thread_is_one_message_per_tweet_with_the_keyboard_on_the_last() -> None:
    ready = make_ready(["Первый твит.", "Второй твит.", "Третий твит."], PostFormat.THREAD)

    outgoing = post_ready_messages(ready)

    assert [m.text for m in outgoing[:3]] == ["Первый твит.", "Второй твит.", "Третий твит."]
    assert [m.keyboard is not None for m in outgoing[:3]] == [False, False, True]
    assert not any(m.html for m in outgoing[:3])


def test_a_numbered_thread_sends_the_numbering() -> None:
    draft = Draft(
        post_format=PostFormat.THREAD,
        parts=[DraftPart(text="Один.", prefix="1/ "), DraftPart(text="Два.", prefix="2/ ")],
        used_fact_ids=[],
        unverified_numbers=[],
        length_violations=[],
        attempts=1,
    )
    ready = make_ready().model_copy(
        update={"result": make_ready().result.model_copy(update={"draft": draft})}
    )

    assert [m.text for m in post_ready_messages(ready)[:2]] == ["1/ Один.", "2/ Два."]


def test_the_post_text_is_sent_unescaped_as_plain_text() -> None:
    ready = make_ready(["Текст <b>с</b> & знаками."])

    first = post_ready_messages(ready)[0]

    assert first.text == "Текст <b>с</b> & знаками."
    assert not first.html


def test_facts_are_split_into_used_and_unused() -> None:
    [text] = facts_messages(make_ready())

    used_at = text.index(messages.USED_SECTION)
    unused_at = text.index(messages.UNUSED_SECTION)
    assert used_at < text.index("<b>F1</b>") < text.index("<b>F3</b>") < unused_at
    assert unused_at < text.index("<b>F2</b>")
    assert messages.FACTS_HEADER_TEMPLATE.format(used=3, total=5) in text


def test_disputed_facts_are_marked_with_the_reason_and_the_other_side() -> None:
    [text] = facts_messages(make_ready())

    assert "<b>F3</b> · СПОРНО" in text
    assert "Почему спорно (вместе с F4): Оценки &lt;расходятся&gt;." in text
    assert "Почему спорно (вместе с F3)" in text
    assert (
        "<b>F5</b> · СПОРНО\nИсточники спорят о месте.\n" + messages.UNGROUPED_DISPUTE_REASON_LINE
        in text
    )


def test_statuses_are_named() -> None:
    [text] = facts_messages(make_ready())

    assert "<b>F1</b> · " + messages.STATUS_LABELS[FactStatus.CONFIRMED] in text
    assert "<b>F2</b> · " + messages.STATUS_LABELS[FactStatus.SINGLE] in text


def test_fact_text_and_links_are_escaped() -> None:
    [text] = facts_messages(make_ready())

    assert "Командовал &lt;князь&gt; &amp; воевода." in text
    assert '<a href="https://history.example.org/page?a=1&amp;b=&lt;2&gt;">' in text
    assert '<a href="https://ru.wikipedia.org/wiki/Битва">ru.wikipedia.org</a>' in text
    assert ">history.example.org</a>" in text
    assert text.count("<a ") == text.count("</a>")


def test_partial_source_failures_are_noted_under_the_facts() -> None:
    [text] = facts_messages(make_ready(failures=[FAILURE, FAILURE]))

    assert text.endswith("Не ответили источники: tavily (SourceUnavailableError) ×2.")


def test_a_variant_shows_only_its_facts_and_counts_the_rest() -> None:
    [text] = facts_messages(make_ready(used=["F3", "F4"], variant=True))

    assert "<b>F3</b> · СПОРНО" in text
    assert "<b>F1</b>" not in text
    assert "<b>F2</b>" not in text
    assert text.endswith(messages.OTHER_FACTS_TEMPLATE.format(count=3))


def test_a_variant_using_every_fact_has_no_rest_line() -> None:
    [text] = facts_messages(make_ready(used=["F1", "F2", "F3", "F4", "F5"], variant=True))

    assert "Остальные" not in text


def test_a_post_without_used_facts_lists_all_as_unused() -> None:
    [text] = facts_messages(make_ready(used=[]))

    assert messages.USED_SECTION not in text
    assert messages.UNUSED_SECTION in text


def test_many_facts_are_split_between_messages_without_breaking_a_fact() -> None:
    long_url = "https://example.org/" + "a" * 200
    facts = [
        make_fact(f"F{index}", "Факт " * 30, FactStatus.SINGLE, long_url, WIKI_URL)
        for index in range(1, 41)
    ]
    ready = make_ready(used=["F1"]).model_copy(
        update={"fact_set": FactSet(topic="t", facts=facts, disputes=[])}
    )

    chunks = facts_messages(ready)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= TELEGRAM_MESSAGE_MAX_CHARS
        assert chunk.count("<a ") == chunk.count("</a>")
        assert chunk.count("<b>") == chunk.count("</b>")
    assert sum(chunk.count("<b>F") for chunk in chunks) == 40


def test_a_block_over_the_limit_is_split_at_lines() -> None:
    block = "\n".join(["строка " * 5] * 4)

    chunks = pack_blocks([block], 80)

    assert all(len(chunk) <= 80 for chunk in chunks)
    assert len(chunks) > 1


def test_a_clean_post_has_no_warnings() -> None:
    assert warnings(make_ready()) == []
    assert all(m.text != messages.WARNINGS_HEADER for m in post_ready_messages(make_ready()))


def test_every_warning_kind_is_shown() -> None:
    critic = Violation(
        rule=StyleRule.CLICHE,
        source=ViolationSource.CRITIC,
        part=2,
        excerpt="вошла в историю",
        explanation="Штамп.",
    )
    length_rule = Violation(
        rule=StyleRule.LENGTH, source=ViolationSource.CODE, part=1, explanation="LEN-DUP"
    )
    number_rule = Violation(
        rule=StyleRule.UNVERIFIED_NUMBER,
        source=ViolationSource.CODE,
        excerpt="1382",
        explanation="NUM-DUP",
    )
    ready = make_ready(
        ["Первый твит.", "Второй твит."],
        PostFormat.THREAD,
        violations=[critic, length_rule, number_rule],
        critic=CriticStatus.FAILED,
        regeneration_failed=True,
        unverified=["1382", "300"],
        length=[
            LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=1, actual=300, limit=280),
            LengthViolation(issue=LengthIssue.TOO_MANY_PARTS, actual=13, limit=12),
        ],
        dropped=["Хвост поста."],
        downgrade=ThreadDowngrade(assertable=3, required=5),
    )

    found = warnings(ready)
    text = "\n".join(found)

    assert "Числа, которых нет в фактах: 1382, 300." in text
    assert "Длина, твит 1: 300 символов при лимите 280." in text
    assert "Длина, в треде 13 твитов при максимуме 12." in text
    assert "Штамп «вошла в историю» (твит 2): Штамп." in text
    assert messages.CRITIC_FAILED_TEXT in found
    assert messages.REGENERATION_FAILED_TEXT in found
    assert "«Хвост поста.»" in text
    assert "мог стать неточным" in text
    assert "нашлось 3" in text
    assert "LEN-DUP" not in text
    assert "NUM-DUP" not in text


def test_a_short_post_names_the_post_not_a_tweet() -> None:
    ready = make_ready(
        length=[LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=1, actual=300, limit=280)],
        violations=[
            Violation(
                rule=StyleRule.FILLER,
                source=ViolationSource.CRITIC,
                part=1,
                excerpt="Это важно",
                explanation="Пустая.",
            )
        ],
    )

    text = "\n".join(warnings(ready))

    assert "Длина, пост: 300 символов при лимите 280." in text
    assert "Пустая фраза «Это важно»: Пустая." in text


def test_a_disabled_critic_is_not_a_warning() -> None:
    assert warnings(make_ready(critic=CriticStatus.DISABLED)) == []


def test_the_message_order_is_post_warnings_facts() -> None:
    outgoing = post_ready_messages(make_ready(unverified=["1"]))

    assert outgoing[0].keyboard is not None
    assert outgoing[1].text.startswith(messages.WARNINGS_HEADER)
    assert outgoing[2].html


def test_every_style_rule_has_a_label() -> None:
    assert set(messages.RULE_LABELS) == set(StyleRule)


def test_every_stage_and_failure_has_a_text() -> None:
    assert set(messages.STAGE_TEXTS) == set(PipelineStage)
    assert set(messages.STAGE_NAMES) == set(PipelineStage)
    assert set(messages.FAILURE_REASONS) == set(FailureKind)
    assert set(messages.ACTION_LABELS) == set(PostAction)


@pytest.mark.parametrize("action", list(PostAction))
def test_callback_data_fits_telegram(action: PostAction) -> None:
    packed = DraftCallback(action=action, draft_id="f" * 12).pack()

    assert len(packed.encode()) <= 64
    assert DraftCallback.unpack(packed) == DraftCallback(action=action, draft_id="f" * 12)


def test_the_keyboard_has_the_given_actions_in_rows_of_two() -> None:
    full = draft_keyboard("abc", list(PostAction))
    thread = draft_keyboard("abc", [PostAction.SHORTER, PostAction.ANGLE, PostAction.VARIANT])

    assert [[b.text for b in row] for row in full.inline_keyboard] == [
        ["короче", "в тред"],
        ["другой заход", "ещё вариант"],
    ]
    assert [len(row) for row in thread.inline_keyboard] == [2, 1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Куликовская битва", TopicRequest(topic="Куликовская битва")),
        (
            "тред: Куликовская битва",
            TopicRequest(topic="Куликовская битва", post_format=PostFormat.THREAD),
        ),
        ("Тред:Битва", TopicRequest(topic="Битва", post_format=PostFormat.THREAD)),
        ("  ЛОНГ :  Битва ", TopicRequest(topic="Битва", post_format=PostFormat.LONG)),
        ("коротко: Битва", TopicRequest(topic="Битва", post_format=PostFormat.SHORT)),
        ("Битва: начало", TopicRequest(topic="Битва: начало")),
        ("тред: Битва: начало", TopicRequest(topic="Битва: начало", post_format=PostFormat.THREAD)),
        ("тред:", TopicRejection.EMPTY),
        ("   ", TopicRejection.EMPTY),
        ("я" * TOPIC_MAX_CHARS, TopicRequest(topic="я" * TOPIC_MAX_CHARS)),
        ("я" * (TOPIC_MAX_CHARS + 1), TopicRejection.TOO_LONG),
        ("тред: " + "я" * (TOPIC_MAX_CHARS + 1), TopicRejection.TOO_LONG),
    ],
)
def test_topic_parsing(text: str, expected: TopicRequest | TopicRejection) -> None:
    assert parse_topic(text) == expected


INSUFFICIENT = NotEnoughFacts(
    extraction=InsufficientFacts(
        fact_set=FACT_SET, assertable_count=2, required=3, stats=ExtractionStats()
    ),
    disputed=3,
    failures=[FAILURE],
)


def test_too_few_facts_says_what_was_found_and_shows_it() -> None:
    rendered = render_topic_outcome(INSUFFICIENT)

    assert rendered.status == messages.NOT_ENOUGH_FACTS_TEMPLATE.format(
        assertable=2, disputed=3, attributed=0, required=3
    )
    [facts] = rendered.messages
    assert facts.html
    assert "<b>F3</b> · СПОРНО" in facts.text
    assert "tavily (SourceUnavailableError)" in facts.text


def test_too_few_facts_with_no_facts_sends_only_the_status() -> None:
    empty = INSUFFICIENT.model_copy(
        update={
            "extraction": InsufficientFacts(
                fact_set=FactSet(topic="t", facts=[], disputes=[]),
                assertable_count=0,
                required=3,
                stats=ExtractionStats(),
            ),
            "failures": [],
        }
    )

    assert render_topic_outcome(empty).messages == []


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (NoSources(), messages.NO_SOURCES_TEXT),
        (ResearchFailed(failures=[FAILURE, FAILURE]), "tavily (SourceUnavailableError) ×2"),
        (NothingFound(failures=[]), messages.NOTHING_FOUND_TEXT),
        (NothingFound(failures=[FAILURE]), "Не ответили источники: tavily"),
        (
            StepFailed(stage=PipelineStage.FACTS, kind=FailureKind.UNAVAILABLE),
            "Шаг «выделение фактов» не удался: модель недоступна.",
        ),
    ],
)
def test_topic_failures_have_a_clear_status(
    outcome: NoSources | ResearchFailed | NothingFound | StepFailed, expected: str
) -> None:
    rendered = render_topic_outcome(outcome)

    assert rendered.status is not None
    assert expected in rendered.status
    assert rendered.messages == []


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (DraftExpired(), messages.STALE_BUTTONS_TEXT),
        (ThreadUnavailable(assertable=3, required=5), "нужно не меньше 5"),
        (
            StepFailed(stage=PipelineStage.WRITING, kind=FailureKind.RATE_LIMIT),
            "Шаг «написание» не удался: превышен лимит запросов к модели.",
        ),
    ],
)
def test_button_failures_have_a_clear_status(
    outcome: DraftExpired | ThreadUnavailable | StepFailed, expected: str
) -> None:
    rendered = render_rework_outcome(outcome)

    assert rendered.status is not None
    assert expected in rendered.status


def test_a_ready_post_has_no_status() -> None:
    assert render_topic_outcome(make_ready()).status is None
    assert render_rework_outcome(make_ready(variant=True)).status is None


def test_a_rejected_regression_is_warned_about_with_a_count_only() -> None:
    ready = make_ready(regressions_rejected=2)

    found = warnings(ready)

    assert found == [messages.REGRESSIONS_REJECTED_TEMPLATE.format(count=2)]
    assert "Правок фильтра стиля отклонено: 2" in found[0]
    assert "слишком сильно сокращали текст, показан предыдущий вариант" in found[0]


def test_no_regression_warning_when_nothing_was_rejected() -> None:
    assert warnings(make_ready()) == []


def test_a_too_short_long_post_names_the_size_and_the_minimum() -> None:
    ready = make_ready(
        post_format=PostFormat.LONG,
        length=[
            LengthViolation(issue=LengthIssue.TOO_SHORT, part=1, actual=640, limit=1200),
            LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=3, limit=6),
        ],
    )

    text = "\n".join(warnings(ready))

    assert "Длина, пост: 640 символов при минимуме 1200, слишком коротко." in text
    assert "Длина, в посте 3 фактов при минимуме 6." in text


WEAK_URL = "https://m.youtube.com/watch?v=1"


def weak_fact(fact_id: str, *, also_plain: bool = False) -> Fact:
    support = [
        SourceRef(snippet_id="s", url=WEAK_URL, domain="youtube.com", quote="цитата", weak=True)
    ]
    if also_plain:
        support.append(source(WIKI_URL))
    return Fact(id=fact_id, text=f"Факт {fact_id}.", status=FactStatus.SINGLE, support=support)


def ready_with(facts: list[Fact], used: list[str]) -> PostReady:
    fact_set = FactSet(topic="Тема", facts=facts, disputes=[])
    return make_ready(used=used).model_copy(update={"fact_set": fact_set})


def test_a_weak_source_is_marked_next_to_its_link() -> None:
    ready = ready_with([weak_fact("F1", also_plain=True)], ["F1"])

    [text] = facts_messages(ready)

    assert f'<a href="{WEAK_URL}">m.youtube.com</a> · слабый' in text
    assert '<a href="https://ru.wikipedia.org/wiki/Битва">ru.wikipedia.org</a>\n' in text + "\n"
    assert text.count("· слабый") == 1


def test_a_plain_source_has_no_weak_mark() -> None:
    [text] = facts_messages(make_ready())

    assert "слабый" not in text


def test_more_than_half_weak_used_facts_give_a_warning_with_counts_only() -> None:
    ready = ready_with(
        [weak_fact("F1"), weak_fact("F2"), make_fact("F3", "Обычный.", FactStatus.SINGLE)],
        ["F1", "F2", "F3"],
    )

    found = warnings(ready)

    assert messages.WEAK_FACTS_TEMPLATE.format(weak=2, total=3) in found
    assert "2 из 3" in "\n".join(found)
    assert "youtube" not in "\n".join(found)


def test_exactly_half_weak_used_facts_give_no_warning() -> None:
    ready = ready_with(
        [weak_fact("F1"), make_fact("F2", "Обычный.", FactStatus.SINGLE)], ["F1", "F2"]
    )

    assert warnings(ready) == []


def test_a_fact_with_one_plain_source_is_not_counted_as_weak() -> None:
    ready = ready_with([weak_fact("F1", also_plain=True)], ["F1"])

    assert warnings(ready) == []


def test_weak_facts_that_are_not_in_the_post_give_no_warning() -> None:
    ready = ready_with(
        [weak_fact("F1"), weak_fact("F2"), make_fact("F3", "Обычный.", FactStatus.SINGLE)],
        ["F3"],
    )

    assert warnings(ready) == []


def test_a_post_without_used_facts_gives_no_weak_warning() -> None:
    ready = ready_with([weak_fact("F1")], [])

    assert warnings(ready) == []


def test_unknown_used_ids_do_not_count() -> None:
    ready = ready_with(
        [weak_fact("F1"), make_fact("F2", "Обычный.", FactStatus.SINGLE)], ["F2", "F9"]
    )

    assert warnings(ready) == []


def test_the_weak_warning_is_sent_among_the_warnings() -> None:
    ready = ready_with([weak_fact("F1")], ["F1"])

    sent = [message.text for message in post_ready_messages(ready)]

    assert any(messages.WARNINGS_HEADER in text and "1 из 1" in text for text in sent)
