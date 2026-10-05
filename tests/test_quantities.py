from fractions import Fraction

import pytest
from app.config.quantities import QuantityKind
from app.services.quantities import (
    Amount,
    amounts_of,
    check_post_quantities,
    checked_quantities,
    exact_tolerance,
    quantities_supported,
    same_value,
)
from app.services.quote_check import extract_numbers, numbers_supported

TOLERANCE = 0.10
SHARE = QuantityKind.SHARE
MULTIPLE = QuantityKind.MULTIPLE
NUMBER = QuantityKind.NUMBER


def found(text: str) -> list[tuple[str, QuantityKind, Fraction]]:
    return [(quantity.form, quantity.kind, quantity.value) for quantity in checked_quantities(text)]


@pytest.mark.parametrize(
    ("text", "form", "kind", "value"),
    [
        ("Половина населения жила в деревне.", "Половина", SHARE, Fraction(1, 2)),
        ("Деревня потеряла половину жителей.", "половину", SHARE, Fraction(1, 2)),
        ("В половине домов не было света.", "половине", SHARE, Fraction(1, 2)),
        ("Он владел половиной земель.", "половиной", SHARE, Fraction(1, 2)),
        ("Около половины голосов ушло центру.", "половины", SHARE, Fraction(1, 2)),
        ("Треть депутатов голосовала против.", "Треть", SHARE, Fraction(1, 3)),
        ("Около трети депутатов голосовали против.", "трети", SHARE, Fraction(1, 3)),
        ("Он владел почти третью земель.", "третью", SHARE, Fraction(1, 3)),
        ("Одна треть депутатов голосовала против.", "Одна треть", SHARE, Fraction(1, 3)),
        ("Две трети семей жили в бараках.", "Две трети", SHARE, Fraction(2, 3)),
        ("Доля достигла двух третей.", "двух третей", SHARE, Fraction(2, 3)),
        ("Он владел двумя третями земель.", "двумя третями", SHARE, Fraction(2, 3)),
        ("Четверть жилья построили заново.", "Четверть", SHARE, Fraction(1, 4)),
        ("Около четверти жилья построили заново.", "четверти", SHARE, Fraction(1, 4)),
        ("Три четверти солдат были новобранцами.", "Три четверти", SHARE, Fraction(3, 4)),
        ("Из трёх четвертей солдат.", "трёх четвертей", SHARE, Fraction(3, 4)),
        ("Пятая часть урожая пропала.", "Пятая часть", SHARE, Fraction(1, 5)),
        ("Он отдал пятую часть урожая.", "пятую часть", SHARE, Fraction(1, 5)),
        ("Страна занимала шестую часть суши.", "шестую часть", SHARE, Fraction(1, 6)),
        ("Без десятой части урожая.", "десятой части", SHARE, Fraction(1, 10)),
        ("Он владел третьей частью земель.", "третьей частью", SHARE, Fraction(1, 3)),
        ("Одна пятая жителей уехала.", "Одна пятая", SHARE, Fraction(1, 5)),
        ("Около одной десятой жителей уехала.", "одной десятой", SHARE, Fraction(1, 10)),
        ("Две пятых жителей уехали.", "Две пятых", SHARE, Fraction(2, 5)),
        ("Каждый второй житель уехал.", "Каждый второй", SHARE, Fraction(1, 2)),
        ("Каждая третья семья уехала.", "Каждая третья", SHARE, Fraction(1, 3)),
        ("У каждого пятого жителя была лошадь.", "каждого пятого", SHARE, Fraction(1, 5)),
        ("Каждый десятый родился за границей.", "Каждый десятый", SHARE, Fraction(1, 10)),
        ("Пять процентов голосов ушло малым партиям.", "Пять процентов", SHARE, Fraction(1, 20)),
        ("Не больше двух процентов голосов.", "двух процентов", SHARE, Fraction(1, 50)),
        ("Выпуск вырос вдвое.", "вдвое", MULTIPLE, Fraction(2)),
        ("Выпуск вырос втрое.", "втрое", MULTIPLE, Fraction(3)),
        ("Выпуск вырос вчетверо.", "вчетверо", MULTIPLE, Fraction(4)),
        ("Выпуск вырос в два раза.", "два раза", MULTIPLE, Fraction(2)),
        ("Выпуск вырос в три раза.", "три раза", MULTIPLE, Fraction(3)),
        ("Выпуск вырос в пять раз.", "пять раз", MULTIPLE, Fraction(5)),
        ("Выпуск вырос в полтора раза.", "полтора раза", MULTIPLE, Fraction(3, 2)),
        ("Город дважды осаждали.", "дважды", MULTIPLE, Fraction(2)),
        ("Город трижды осаждали.", "трижды", MULTIPLE, Fraction(3)),
        ("Там жили полтора миллиона человек.", "полтора", NUMBER, Fraction(3, 2)),
        ("Стройка шла полторы недели.", "полторы", NUMBER, Fraction(3, 2)),
        ("Около полутора миллионов человек.", "полутора", NUMBER, Fraction(3, 2)),
        ("ПОЛОВИНА населения.", "ПОЛОВИНА", SHARE, Fraction(1, 2)),
        ("Почти треть поляков.", "треть", SHARE, Fraction(1, 3)),
        ("Почти тре́ть поляков.", "треть", SHARE, Fraction(1, 3)),
    ],
)
def test_each_form_has_its_value(text: str, form: str, kind: QuantityKind, value: Fraction) -> None:
    assert found(text) == [(form, kind, value)]


def test_qualifiers_are_not_part_of_the_quantity() -> None:
    assert found("Почти треть, около половины, более четверти и примерно вдвое.") == [
        ("треть", SHARE, Fraction(1, 3)),
        ("половины", SHARE, Fraction(1, 2)),
        ("четверти", SHARE, Fraction(1, 4)),
        ("вдвое", MULTIPLE, Fraction(2)),
    ]


def test_the_longest_phrase_wins() -> None:
    assert found("Две трети и одна пятая часть, в полтора раза.") == [
        ("Две трети", SHARE, Fraction(2, 3)),
        ("одна пятая", SHARE, Fraction(1, 5)),
        ("полтора раза", MULTIPLE, Fraction(3, 2)),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Это было в первой половине дня.",
        "Во второй половине 1930-х годов строили много.",
        "Вторая половина 1930-х прошла в стройках.",
        "Государство второй половины XX века.",
        "С первой половины XIX века.",
        "Встреча была в половине шестого.",
        "Было половина шестого.",
        "Ждали половину часа.",
        "Прошла четверть часа.",
        "Прошла четверть века.",
        "Это было в первой четверти XX века.",
        "В последней трети века.",
        "Две трети века прошли в войнах.",
        "Завод ввёл в строй третью доменную печь.",
        "Третьяковская галерея открылась.",
        "Третий член экипажа остался на орбите.",
        "Ольгерд в третий раз пошёл на Москву.",
        "Вторая и третья пятилетки.",
        "Рекорд не мог быть установлен в два разных дня.",
        "Это были половинчатые меры.",
        "Раз в год он ездил в город.",
        "Пятый полк и шестая рота.",
        "Он выиграл процент по вкладу.",
        "Каждый год он ездил в город.",
    ],
)
def test_non_quantity_uses_are_ignored(text: str) -> None:
    assert found(text) == []


@pytest.mark.parametrize(
    ("first", "second", "matches"),
    [
        (Fraction(1, 3), Fraction(33, 100), True),
        (Fraction(1, 3), Fraction(3, 10), True),
        (Fraction(1, 3), Fraction(374, 1000), False),
        (Fraction(1, 3), Fraction(2, 5), False),
        (Fraction(1, 2), Fraction(45, 100), True),
        (Fraction(1, 2), Fraction(2, 5), False),
        (Fraction(1, 4), Fraction(1, 3), False),
        (Fraction(1, 5), Fraction(1, 6), False),
        (Fraction(2), Fraction(2), True),
    ],
)
def test_relative_tolerance_against_the_larger_value(
    first: Fraction, second: Fraction, matches: bool
) -> None:
    tolerance = exact_tolerance(TOLERANCE)

    assert same_value(first, second, tolerance) is matches
    assert same_value(second, first, tolerance) is matches


def test_tolerance_is_exact() -> None:
    assert exact_tolerance(0.1) == Fraction(1, 10)


@pytest.mark.parametrize("quote", ["33%", "33 %", "1/3", "33 процента", "треть", "a third"])
def test_third_matches_its_equivalents(quote: str) -> None:
    assert quantities_supported("Партия получила треть голосов", [f"Итог: {quote}"], TOLERANCE)


@pytest.mark.parametrize("quote", ["40%", "40 процентов", "2/5", "четверть", "половина"])
def test_third_does_not_match_other_shares(quote: str) -> None:
    assert not quantities_supported("Партия получила треть голосов", [f"Итог: {quote}"], TOLERANCE)


def test_thirty_percent_and_thirty_procentov_are_the_same_share() -> None:
    assert amounts_of("30%", english=False) == amounts_of("30 процентов", english=False)
    assert Amount(SHARE, Fraction(3, 10)) in amounts_of("30 процентов", english=False)


@pytest.mark.parametrize(
    ("fact_text", "quote"),
    [
        ("Город дважды осаждали", "город осаждали 2 раза"),
        ("Выпуск вырос вдвое", "выпуск вырос в 2 раза"),
        ("Выпуск вырос в два раза", "выпуск вырос вдвое"),
        ("Там жили полтора миллиона человек", "там жили 1,5 миллиона человек"),
        ("Половина жителей уехала", "уехали 50 % жителей"),
    ],
)
def test_same_kind_supports(fact_text: str, quote: str) -> None:
    assert quantities_supported(fact_text, [quote], TOLERANCE)


@pytest.mark.parametrize(
    ("fact_text", "quote"),
    [
        ("Ольгерд дважды ходил на Москву", "Ольгерд ходил на Москву, было 2 похода"),
        ("Половина жителей уехала", "уехали 50 жителей"),
        ("Выпуск вырос вдвое", "выпуск составил 2 тысячи"),
        ("Каждый пятый уехал", "уехали 5 человек"),
        ("Половина жителей уехала", "the population doubled"),
    ],
)
def test_other_kinds_do_not_support(fact_text: str, quote: str) -> None:
    assert not quantities_supported(fact_text, [quote], TOLERANCE)


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("About a third of the voters", Amount(SHARE, Fraction(1, 3))),
        ("one-third of the land", Amount(SHARE, Fraction(1, 3))),
        ("two thirds of the families", Amount(SHARE, Fraction(2, 3))),
        ("two-thirds of the families", Amount(SHARE, Fraction(2, 3))),
        ("half of the population", Amount(SHARE, Fraction(1, 2))),
        ("a quarter of the housing", Amount(SHARE, Fraction(1, 4))),
        ("three quarters of the soldiers", Amount(SHARE, Fraction(3, 4))),
        ("a sixth of the land surface", Amount(SHARE, Fraction(1, 6))),
        ("one in ten was born abroad", Amount(SHARE, Fraction(1, 10))),
        ("37.4 percent of the vote", Amount(SHARE, Fraction(374, 1000))),
        ("37.4 per cent of the vote", Amount(SHARE, Fraction(374, 1000))),
        ("output doubled", Amount(MULTIPLE, Fraction(2))),
        ("the city was besieged twice", Amount(MULTIPLE, Fraction(2))),
        ("three times as many", Amount(MULTIPLE, Fraction(3))),
        ("13 times the norm", Amount(MULTIPLE, Fraction(13))),
    ],
)
def test_english_forms_give_support(text: str, amount: Amount) -> None:
    assert amount in amounts_of(text, english=True)


@pytest.mark.parametrize(
    "text",
    [
        "in the first half of the 1930s",
        "the second half of the century",
        "half an hour later",
        "half past five",
        "a quarter century later",
    ],
)
def test_english_periods_give_no_share(text: str) -> None:
    assert not any(amount.kind is SHARE for amount in amounts_of(text, english=True))


def test_english_forms_are_support_only() -> None:
    assert checked_quantities("About a third of the voters, twice") == []
    assert amounts_of("About a third of the voters", english=False) == set()


def test_english_quote_supports_a_russian_share() -> None:
    assert quantities_supported(
        "Около трети голосов получила партия", ["About a third of the votes went"], TOLERANCE
    )


def test_a_fact_without_word_quantities_passes() -> None:
    assert quantities_supported("Битва была 8 сентября 1380 года", [], TOLERANCE)


def test_a_word_quantity_without_any_quote_fails() -> None:
    assert not quantities_supported("Треть войска погибла", [], TOLERANCE)


def test_every_word_quantity_of_a_fact_needs_support() -> None:
    quotes = ["Треть войска погибла"]

    assert quantities_supported("Треть войска погибла", quotes, TOLERANCE)
    assert not quantities_supported("Треть войска погибла, половина ушла", quotes, TOLERANCE)


@pytest.mark.parametrize(
    "text",
    ["треть", "половина", "в 13 раз", "вдвое", "1/3", "30 процентов", "30%", "1320-1330"],
)
def test_digit_extraction_is_unchanged(text: str) -> None:
    expected = {
        "треть": set(),
        "половина": set(),
        "в 13 раз": {"13"},
        "вдвое": set(),
        "1/3": {"1", "3"},
        "30 процентов": {"30"},
        "30%": {"30"},
        "1320-1330": {"1320", "1330"},
    }[text]

    assert extract_numbers(text) == expected


def test_digit_rule_of_the_fact_step_is_unchanged() -> None:
    assert not numbers_supported("Партия получила 33% голосов", ["a third of the votes"])
    assert numbers_supported("Партия получила 30% голосов", ["30 процентов голосов"])


def post(text: str, *facts: str) -> tuple[list[str], list[list[str]]]:
    check = check_post_quantities([text], list(facts), TOLERANCE)
    return check.unsupported, check.share_sets


@pytest.mark.parametrize(
    ("fact", "supported_by_fact"),
    [
        ("Партия получила 30% голосов.", True),
        ("Партия получила 30 процентов голосов.", True),
        ("Партия получила треть голосов.", True),
        ("Партия получила 1/3 голосов.", True),
        ("Партия получила 37,4% голосов.", False),
        ("Партия получила 40% голосов.", False),
        ("Партия получила 30 мест.", False),
        ("Выборы прошли в 1932 году.", False),
    ],
)
def test_about_a_third_in_a_post(fact: str, supported_by_fact: bool) -> None:
    unsupported, _ = post("Около трети голосов получила партия.", fact)

    assert unsupported == ([] if supported_by_fact else ["трети"])


@pytest.mark.parametrize(
    ("fact", "supported_by_fact"),
    [("За неё было 45% голосов.", True), ("За неё было 40% голосов.", False)],
)
def test_a_half_in_a_post(fact: str, supported_by_fact: bool) -> None:
    unsupported, _ = post("За неё была половина голосов.", fact)

    assert unsupported == ([] if supported_by_fact else ["половина"])


def test_a_digit_percent_in_a_post_is_not_checked_as_a_word() -> None:
    assert post("Партия получила 33% голосов.", "Партия получила треть голосов.") == ([], [])


def test_unsupported_forms_are_listed_once_as_written() -> None:
    unsupported, _ = post("Треть ушла. Потом ещё треть. Вдвое меньше осталось.", "Без долей.")

    assert unsupported == ["Треть", "Вдвое"]


PICTURE = (
    "Около трети стояли за партию плуга, шестая часть за партию моста, "
    "а около половины составляли центр."
)
PICTURE_FORMS = ["трети", "шестая часть", "половины"]


def test_a_whole_picture_without_fact_support_is_one_set() -> None:
    unsupported, sets = post(PICTURE, "Выборы прошли в 1932 году.")

    assert unsupported == PICTURE_FORMS
    assert sets == [PICTURE_FORMS]


def test_a_partly_supported_picture_flags_every_member() -> None:
    unsupported, sets = post(PICTURE, "Партия плуга получила 30% голосов.")

    assert sets == [PICTURE_FORMS]
    assert unsupported == PICTURE_FORMS


def test_a_picture_stated_by_the_facts_passes() -> None:
    facts = (
        "Партия плуга получила треть голосов.",
        "Партия моста получила 16,7% голосов.",
        "Центр получил половину голосов.",
    )

    assert post(PICTURE, *facts) == ([], [])


def test_digit_percents_that_add_up_to_a_whole_need_shares_in_the_facts() -> None:
    text = "Плуг получил 37%, мост 15%, центр 48%."
    loose = ("Было 37 депутатов, 15 министров и 48 губерний.",)
    shares = ("Плуг получил 37%.", "Мост получил 15%.", "Центр получил 48%.")

    assert post(text, *loose) == (["37%", "15%", "48%"], [["37%", "15%", "48%"]])
    assert post(text, *shares) == ([], [])


def test_the_picture_is_found_across_sentences_of_one_paragraph() -> None:
    text = "Треть была за плуг. Шестая часть за мост. Половина за центр."

    _, sets = post(text, "Без долей.")

    assert sets == [["Треть", "Шестая часть", "Половина"]]


def test_shares_in_different_paragraphs_are_not_one_picture() -> None:
    text = "Треть была за плуг.\n\nШестая часть за мост.\n\nПоловина за центр."

    unsupported, sets = post(text, "Без долей.")

    assert sets == []
    assert unsupported == ["Треть", "Шестая часть", "Половина"]


@pytest.mark.parametrize(
    "text",
    [
        "Треть была за плуг, половина за центр.",
        "Треть была за плуг, четверть за мост, пятая часть за центр.",
        "Половина, половина и половина.",
    ],
)
def test_shares_that_do_not_add_up_to_a_whole_are_not_a_picture(text: str) -> None:
    _, sets = post(text, "Без долей.")

    assert sets == []


def test_a_picture_in_each_tweet_is_checked() -> None:
    check = check_post_quantities(["Без долей.", PICTURE], ["Без долей."], TOLERANCE)

    assert check.share_sets == [PICTURE_FORMS]


def test_empty_post_and_empty_facts() -> None:
    assert check_post_quantities([], [], TOLERANCE) == ([], [])
    assert check_post_quantities([""], [], TOLERANCE) == ([], [])
