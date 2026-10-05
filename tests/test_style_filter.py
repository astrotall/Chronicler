import pytest
from app.config import style
from app.domain.draft import LengthIssue, LengthViolation, PostFormat
from app.domain.style import StyleRule, ViolationSource
from app.services.style_filter import check_draft, find_phrases

from style_helpers import make_draft

CLEAN_TEXT = (
    "8 сентября 1380 года на Куликовом поле сошлись два войска. Русским командовал "
    "Дмитрий Иванович, кто-то из летописцев назвал битву Мамаевым побоищем.\n\n"
    "Перед сражением инок Пересвет бился с Челубеем."
)


def rules_of(texts: list[str], *, allow_closing_question: bool = False) -> list[StyleRule]:
    draft = make_draft(texts)
    return [
        violation.rule
        for violation in check_draft(draft, allow_closing_question=allow_closing_question)
    ]


def excerpts_of(text: str, rule: StyleRule) -> list[str | None]:
    return [
        violation.excerpt for violation in check_draft(make_draft([text])) if violation.rule is rule
    ]


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Победа — это начало.", StyleRule.DASH),
        ("Годы 1320–1330 были тихими.", StyleRule.DASH),
        ("Это не просто крепость, а символ эпохи.", StyleRule.BANNED_PHRASE),
        ("Давайте разберёмся, как это было.", StyleRule.BANNED_PHRASE),
        ("ДАВАЙТЕ ПОГРУЗИМСЯ в эпоху.", StyleRule.BANNED_PHRASE),
        ("Стоит отметить, что войско стояло у Дона.", StyleRule.BANNED_PHRASE),
        ("В заключение скажу о Донском.", StyleRule.BANNED_PHRASE),
        ("Знали ли вы, что битва шла у Дона.", StyleRule.BANNED_PHRASE),
        ("Я видел эти стены своими глазами.", StyleRule.INVENTED_EXPERIENCE),
        ("Мне довелось стоять на этом поле.", StyleRule.INVENTED_EXPERIENCE),
        ("Битва у Дона 🔥 решила спор.", StyleRule.EMOJI),
        ("Битва у Дона решила спор. #история", StyleRule.HASHTAG),
        ("Битва у Дона решила спор. А вы знали об этом?", StyleRule.CLOSING_QUESTION),
    ],
)
def test_each_deterministic_rule_catches_its_sample(text: str, rule: StyleRule) -> None:
    assert rules_of([text]) == [rule]


def test_a_clean_sample_has_no_violation() -> None:
    assert rules_of([CLEAN_TEXT]) == []


@pytest.mark.parametrize(
    "text",
    [
        "Победа - это начало.",
        "Кто-то пришёл по-петровски.",
        "Годы 1320-1330 были тихими.",
        "Спор шёл о 60-150 тысячах.",
    ],
)
def test_a_hyphen_is_never_a_dash_violation(text: str) -> None:
    assert rules_of([text]) == []


@pytest.mark.parametrize("dash", ["—", "–"])
def test_a_spaced_em_or_en_dash_is_a_violation_with_context(dash: str) -> None:
    [violation] = check_draft(make_draft([f"Победа {dash} это начало большой войны."]))

    assert violation.rule is StyleRule.DASH
    assert violation.source is ViolationSource.CODE
    assert violation.part == 1
    assert violation.excerpt == f"Победа {dash} это начало большой"
    assert dash in violation.explanation


def test_every_dash_is_its_own_violation() -> None:
    assert rules_of(["Первое — второе — третье."]) == [StyleRule.DASH, StyleRule.DASH]


def test_three_names_from_the_facts_are_not_flagged() -> None:
    assert rules_of(["В экипаж входили Армстронг, Олдрин и Коллинз."]) == []


@pytest.mark.parametrize(
    ("text", "excerpt"),
    [
        ("Это не просто крепость, а символ эпохи.", "Это не просто крепость, а символ"),
        ("Он был не просто город, а крепость.", "не просто город, а крепость"),
        ("Он не просто жил, а работал на двух заводах.", "не просто жил, а работал"),
        (
            "Это не просто большая древняя крепость, а символ.",
            "Это не просто большая древняя крепость, а символ",
        ),
    ],
)
def test_both_not_just_frames_are_caught_once(text: str, excerpt: str) -> None:
    assert excerpts_of(text, StyleRule.BANNED_PHRASE) == [excerpt]


@pytest.mark.parametrize(
    "text",
    [
        "Это было непросто.",
        "Он погиб не просто так.",
        "Он погиб не просто так. А потом, а вот и войско.",
        "Не просто так, говорили летописцы.",
    ],
)
def test_not_just_without_a_after_a_comma_in_the_sentence_is_fine(text: str) -> None:
    assert excerpts_of(text, StyleRule.BANNED_PHRASE) == []


@pytest.mark.parametrize(
    "text",
    [
        "Он провёл 10 лет в заключении.",
        "Князь участвовал в заключении мира.",
    ],
)
def test_an_exact_word_does_not_match_other_forms(text: str) -> None:
    assert rules_of([text]) == []


def test_the_exact_word_still_matches_ignoring_case() -> None:
    assert excerpts_of("В ЗАКЛЮЧЕНИЕ скажу.", StyleRule.BANNED_PHRASE) == ["В ЗАКЛЮЧЕНИЕ"]


@pytest.mark.parametrize(
    ("text", "excerpt"),
    [
        ("Давайте разберемся.", "Давайте разберемся"),
        ("Давай разберём по порядку.", "Давай разберём"),
        ("Стоило отметить и другое.", "Стоило отметить"),
        ("Я видела эти стены.", "Я видела"),
    ],
)
def test_phrases_tolerate_word_forms_and_yo(text: str, excerpt: str) -> None:
    found = find_phrases(
        text,
        (*style.BANNED_PHRASES, *style.INVENTED_EXPERIENCE_PHRASES),
        style.BANNED_PHRASE_EXACT_WORDS,
    )

    assert found == [excerpt]


def test_a_phrase_does_not_span_two_sentences() -> None:
    assert rules_of(["Стоит. Отметить надо другое."]) == []


def test_a_new_banned_phrase_is_caught_from_data_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "История знает много примеров храбрости."
    assert rules_of([text]) == []

    monkeypatch.setattr(style, "BANNED_PHRASES", (*style.BANNED_PHRASES, "история знает"))

    assert excerpts_of(text, StyleRule.BANNED_PHRASE) == ["История знает"]


def test_a_new_invented_experience_phrase_is_caught_from_data_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        style,
        "INVENTED_EXPERIENCE_PHRASES",
        (*style.INVENTED_EXPERIENCE_PHRASES, "я побывал"),
    )

    assert rules_of(["Я побывал на поле."]) == [StyleRule.INVENTED_EXPERIENCE]


def test_a_new_forbidden_dash_is_caught_from_data_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(style, "FORBIDDEN_DASHES", (*style.FORBIDDEN_DASHES, "―"))

    assert rules_of(["Победа ― начало."]) == [StyleRule.DASH]


def test_a_closing_question_is_allowed_by_the_caller() -> None:
    assert rules_of(["Битва у Дона. Почему Мамай ждал?"], allow_closing_question=True) == []


def test_a_question_inside_the_post_is_fine() -> None:
    assert rules_of(["Почему Мамай ждал? Ягайло не успел."]) == []


def test_a_quoted_question_at_the_end_is_not_a_question_to_the_reader() -> None:
    assert rules_of(["Князь спросил: «Где войско?»"]) == []


def test_a_closing_question_is_checked_in_the_last_tweet_only() -> None:
    draft = make_draft(["Почему Мамай ждал?", "Ягайло не успел.", "А вы бы ждали?"])

    [violation] = check_draft(draft)

    assert violation.rule is StyleRule.CLOSING_QUESTION
    assert violation.part == 3
    assert violation.excerpt == "А вы бы ждали?"


def test_parts_are_checked_separately_with_their_number() -> None:
    draft = make_draft(["Битва шла у Дона.", "Победа — начало."])

    [violation] = check_draft(draft)

    assert violation.part == 2


def test_numbering_prefixes_are_not_checked() -> None:
    draft = make_draft(["Битва шла у Дона.", "Победа пришла."], PostFormat.THREAD)
    numbered = draft.model_copy(
        update={"parts": [part.model_copy(update={"prefix": "1— "}) for part in draft.parts]}
    )

    assert check_draft(numbered) == []


def test_length_violations_come_from_the_draft() -> None:
    draft = make_draft(
        ["Битва шла у Дона."],
        length_violations=[
            LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=1, actual=312, limit=280)
        ],
    )

    [violation] = check_draft(draft)

    assert violation.rule is StyleRule.LENGTH
    assert violation.part == 1
    assert violation.excerpt is None
    assert "312" in violation.explanation
    assert "280" in violation.explanation


def test_too_many_tweets_is_a_length_violation_without_a_part() -> None:
    draft = make_draft(
        ["Битва шла у Дона.", "Победа пришла."],
        length_violations=[LengthViolation(issue=LengthIssue.TOO_MANY_PARTS, actual=14, limit=12)],
    )

    [violation] = check_draft(draft)

    assert violation.part is None
    assert "14" in violation.explanation


def test_each_unverified_number_is_a_violation() -> None:
    draft = make_draft(["Битва шла у Дона."], unverified_numbers=["2", "1382"])

    violations = check_draft(draft)

    assert [violation.rule for violation in violations] == [StyleRule.UNVERIFIED_NUMBER] * 2
    assert [violation.excerpt for violation in violations] == ["2", "1382"]


def test_a_word_quantity_is_a_violation_with_its_form() -> None:
    draft = make_draft(["Около трети ушло."], unverified_numbers=["трети"])

    [violation] = check_draft(draft)

    assert violation.rule is StyleRule.UNVERIFIED_NUMBER
    assert violation.excerpt == "трети"
    assert violation.explanation == "Числа или доли «трети» нет ни в одном факте."


def test_a_share_set_is_one_violation_that_names_every_share() -> None:
    forms = ["трети", "шестая часть", "половины"]
    draft = make_draft(
        ["Около трети, шестая часть и около половины. В 1382 году."],
        unverified_numbers=["1382", "Трети", *forms[1:]],
        unverified_share_sets=[forms],
    )

    violations = check_draft(draft)

    assert [violation.excerpt for violation in violations] == ["1382", None]
    explanation = violations[1].explanation
    assert "«трети», «шестая часть», «половины»" in explanation
    assert "складываются в целое" in explanation
    assert "не одну долю" in explanation


def test_an_ampersand_entity_and_a_language_name_are_not_hashtags() -> None:
    assert rules_of(["Код на C# и знак &#123; в тексте."]) == []


@pytest.mark.parametrize("text", ["Температура 30° и №5.", "Знак © в тексте."])
def test_degree_number_and_copyright_signs_are_not_emoji(text: str) -> None:
    assert rules_of([text]) == []
