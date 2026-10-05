from enum import StrEnum
from fractions import Fraction
from typing import NamedTuple


class QuantityKind(StrEnum):
    SHARE = "share"
    MULTIPLE = "multiple"
    NUMBER = "number"


class WordForms(NamedTuple):
    stem: str
    endings: tuple[str, ...] = ("",)


class QuantityPhrase(NamedTuple):
    words: tuple[WordForms, ...]
    kind: QuantityKind
    value: Fraction


NOUN_SOFT_SINGULAR: tuple[str, ...] = ("ь", "и", "ью")
NOUN_SOFT_PLURAL: tuple[str, ...] = ("и", "ей", "ям", "ями", "ях")
HALF_ENDINGS: tuple[str, ...] = ("а", "ы", "е", "у", "ой", "ою")
FRACTION_SINGULAR: tuple[str, ...] = ("ая", "ой", "ую", "ою")
FRACTION_PLURAL: tuple[str, ...] = ("ых", "ым", "ыми")
ADJECTIVE_ENDINGS: tuple[str, ...] = ("ый", "ая", "ое", "ого", "ой", "ому", "ую", "ым", "ом")
STRESSED_ADJECTIVE_ENDINGS: tuple[str, ...] = ("ой", "ая", "ое", "ого", "ому", "ую", "ым", "ом")
THIRD_ORDINAL_ENDINGS: tuple[str, ...] = (
    "ий",
    "ья",
    "ье",
    "ьего",
    "ьей",
    "ьему",
    "ью",
    "ьим",
    "ьем",
)
CARDINAL_SOFT_ENDINGS: tuple[str, ...] = ("ь", "и", "ью")
PLURAL_SUFFIX: tuple[str, ...] = ("", "s")

HALF = WordForms("половин", HALF_ENDINGS)
THIRD = WordForms("трет", NOUN_SOFT_SINGULAR)
THIRDS = WordForms("трет", NOUN_SOFT_PLURAL)
QUARTER = WordForms("четверт", NOUN_SOFT_SINGULAR)
QUARTERS = WordForms("четверт", NOUN_SOFT_PLURAL)
PART = WordForms("част", NOUN_SOFT_SINGULAR)
EACH = WordForms("кажд", ADJECTIVE_ENDINGS)
TIMES = WordForms("раз", ("", "а"))
PERCENT = WordForms("процент", ("", "а", "у", "ом", "е", "ов", "ам", "ами", "ах"))
ONE_AND_HALF: tuple[WordForms, ...] = (
    WordForms("полтор", ("а", "ы")),
    WordForms("полутор", ("а",)),
)
ONE_AND_HALF_VALUE = Fraction(3, 2)

NUMERATOR_ONE = WordForms("одн", ("а", "ой", "у", "ою"))
SMALL_NUMERATORS: dict[int, WordForms] = {
    2: WordForms("дв", ("е", "ух", "ум", "умя")),
    3: WordForms("тр", ("и", "ех", "ем", "емя")),
    4: WordForms("четыр", ("е", "ех", "ем", "ьмя")),
}
NOUN_DENOMINATORS: dict[int, tuple[WordForms, WordForms]] = {
    3: (THIRD, THIRDS),
    4: (QUARTER, QUARTERS),
}
ORDINAL_STEMS: dict[int, str] = {
    5: "пят",
    6: "шест",
    7: "седьм",
    8: "восьм",
    9: "девят",
    10: "десят",
    100: "сот",
}
PART_ORDINALS: dict[int, WordForms] = {
    3: WordForms("трет", ("ья", "ьей", "ью", "ьею")),
    4: WordForms("четверт", FRACTION_SINGULAR),
    **{
        denominator: WordForms(stem, FRACTION_SINGULAR)
        for denominator, stem in ORDINAL_STEMS.items()
    },
}
EVERY_ORDINALS: dict[int, WordForms] = {
    2: WordForms("втор", STRESSED_ADJECTIVE_ENDINGS),
    3: WordForms("трет", THIRD_ORDINAL_ENDINGS),
    4: WordForms("четверт", ADJECTIVE_ENDINGS),
    5: WordForms("пят", ADJECTIVE_ENDINGS),
    6: WordForms("шест", ADJECTIVE_ENDINGS),
    7: WordForms("седьм", STRESSED_ADJECTIVE_ENDINGS),
    8: WordForms("восьм", STRESSED_ADJECTIVE_ENDINGS),
    9: WordForms("девят", ADJECTIVE_ENDINGS),
    10: WordForms("десят", ADJECTIVE_ENDINGS),
    100: WordForms("сот", ADJECTIVE_ENDINGS),
}
CARDINALS: dict[int, WordForms] = {
    1: WordForms("од", ("ин", "ного", "ному", "ним", "ном")),
    2: WordForms("дв", ("а", "е", "ух", "ум", "умя")),
    3: WordForms("тр", ("и", "ех", "ем", "емя")),
    4: WordForms("четыр", ("е", "ех", "ем", "ьмя")),
    5: WordForms("пят", CARDINAL_SOFT_ENDINGS),
    6: WordForms("шест", CARDINAL_SOFT_ENDINGS),
    7: WordForms("сем", CARDINAL_SOFT_ENDINGS),
    8: WordForms("вос", ("емь", "ьми", "емью", "ьмью")),
    9: WordForms("девят", CARDINAL_SOFT_ENDINGS),
    10: WordForms("десят", CARDINAL_SOFT_ENDINGS),
}
MULTIPLE_MIN_CARDINAL = 2
MULTIPLE_WORDS: dict[str, int] = {
    "вдвое": 2,
    "втрое": 3,
    "вчетверо": 4,
    "впятеро": 5,
    "вшестеро": 6,
    "всемеро": 7,
    "ввосьмеро": 8,
    "вдевятеро": 9,
    "вдесятеро": 10,
    "дважды": 2,
    "трижды": 3,
    "четырежды": 4,
}
PERCENT_BASE = 100


def share(numerator: int, denominator: int, *words: WordForms) -> QuantityPhrase:
    return QuantityPhrase(words, QuantityKind.SHARE, Fraction(numerator, denominator))


RUSSIAN_PHRASES: tuple[QuantityPhrase, ...] = (
    share(1, 2, HALF),
    *(share(1, d, singular) for d, (singular, _) in NOUN_DENOMINATORS.items()),
    *(share(1, d, NUMERATOR_ONE, singular) for d, (singular, _) in NOUN_DENOMINATORS.items()),
    *(
        share(n, d, numerator, plural)
        for d, (_, plural) in NOUN_DENOMINATORS.items()
        for n, numerator in SMALL_NUMERATORS.items()
        if n < d
    ),
    *(share(1, d, ordinal, PART) for d, ordinal in PART_ORDINALS.items()),
    *(
        share(1, d, NUMERATOR_ONE, WordForms(stem, FRACTION_SINGULAR))
        for d, stem in ORDINAL_STEMS.items()
    ),
    *(
        share(n, d, numerator, WordForms(stem, FRACTION_PLURAL))
        for d, stem in ORDINAL_STEMS.items()
        for n, numerator in SMALL_NUMERATORS.items()
    ),
    *(share(1, d, EACH, ordinal) for d, ordinal in EVERY_ORDINALS.items()),
    *(share(n, PERCENT_BASE, cardinal, PERCENT) for n, cardinal in CARDINALS.items()),
    *(
        QuantityPhrase((WordForms(word),), QuantityKind.MULTIPLE, Fraction(value))
        for word, value in MULTIPLE_WORDS.items()
    ),
    *(
        QuantityPhrase((cardinal, TIMES), QuantityKind.MULTIPLE, Fraction(n))
        for n, cardinal in CARDINALS.items()
        if n >= MULTIPLE_MIN_CARDINAL
    ),
    *(
        QuantityPhrase((word, TIMES), QuantityKind.MULTIPLE, ONE_AND_HALF_VALUE)
        for word in ONE_AND_HALF
    ),
    *(QuantityPhrase((word,), QuantityKind.NUMBER, ONE_AND_HALF_VALUE) for word in ONE_AND_HALF),
)

ENGLISH_ONE: tuple[WordForms, ...] = (WordForms("a"), WordForms("one"))
ENGLISH_CARDINALS: dict[int, WordForms] = {
    2: WordForms("two"),
    3: WordForms("three"),
    4: WordForms("four"),
    5: WordForms("five"),
    6: WordForms("six"),
    7: WordForms("seven"),
    8: WordForms("eight"),
    9: WordForms("nine"),
    10: WordForms("ten"),
}
ENGLISH_DENOMINATORS: dict[int, tuple[WordForms, ...]] = {
    3: (WordForms("third", PLURAL_SUFFIX),),
    4: (WordForms("quarter", PLURAL_SUFFIX), WordForms("fourth", PLURAL_SUFFIX)),
    5: (WordForms("fifth", PLURAL_SUFFIX),),
    6: (WordForms("sixth", PLURAL_SUFFIX),),
    7: (WordForms("seventh", PLURAL_SUFFIX),),
    8: (WordForms("eighth", PLURAL_SUFFIX),),
    9: (WordForms("ninth", PLURAL_SUFFIX),),
    10: (WordForms("tenth", PLURAL_SUFFIX),),
}
ENGLISH_HALF = WordForms("half")
ENGLISH_IN = WordForms("in")
ENGLISH_TIMES = WordForms("times")
ENGLISH_MULTIPLE_WORDS: dict[str, int] = {
    "twice": 2,
    "double": 2,
    "doubled": 2,
    "twofold": 2,
    "triple": 3,
    "tripled": 3,
    "threefold": 3,
    "quadrupled": 4,
    "fourfold": 4,
}

ENGLISH_PHRASES: tuple[QuantityPhrase, ...] = (
    share(1, 2, ENGLISH_HALF),
    *(
        share(1, d, one, denominator)
        for d, denominators in ENGLISH_DENOMINATORS.items()
        for denominator in denominators
        for one in ENGLISH_ONE
    ),
    *(
        share(n, d, numerator, denominator)
        for d, denominators in ENGLISH_DENOMINATORS.items()
        for denominator in denominators
        for n, numerator in ENGLISH_CARDINALS.items()
        if n < d
    ),
    *(
        share(1, d, ENGLISH_ONE[1], ENGLISH_IN, cardinal)
        for d, cardinal in ENGLISH_CARDINALS.items()
    ),
    *(
        QuantityPhrase((WordForms(word),), QuantityKind.MULTIPLE, Fraction(value))
        for word, value in ENGLISH_MULTIPLE_WORDS.items()
    ),
    *(
        QuantityPhrase((cardinal, ENGLISH_TIMES), QuantityKind.MULTIPLE, Fraction(n))
        for n, cardinal in ENGLISH_CARDINALS.items()
    ),
)

PERCENT_SIGNS: tuple[str, ...] = ("%",)
PERCENT_WORDS: tuple[WordForms, ...] = (
    PERCENT,
    WordForms("percent"),
    WordForms("per cent"),
)
TIMES_WORDS: tuple[WordForms, ...] = (TIMES, ENGLISH_TIMES)
FRACTION_SLASHES: tuple[str, ...] = ("/", "⁄")
FRACTION_MAX_DENOMINATOR = 100

PERIOD_FEMININE_ENDINGS: tuple[str, ...] = ("ая", "ой", "ую", "ою", "ые", "ых", "ым", "ыми")
PERIOD_ORDINALS: tuple[WordForms, ...] = (
    WordForms("перв", PERIOD_FEMININE_ENDINGS),
    WordForms("втор", PERIOD_FEMININE_ENDINGS),
    WordForms("трет", ("ья", "ьей", "ью", "ьею", "ьи", "ьих")),
    WordForms("четверт", PERIOD_FEMININE_ENDINGS),
    WordForms("последн", ("яя", "ей", "юю", "ею", "ие", "их")),
    WordForms("first"),
    WordForms("second"),
    WordForms("third"),
    WordForms("fourth"),
    WordForms("last"),
    WordForms("latter"),
    WordForms("former"),
)
TIME_UNITS: tuple[WordForms, ...] = (
    WordForms("час", ("а",)),
    WordForms("дн", ("я",)),
    WordForms("сут", ("ок",)),
    WordForms("недел", ("и",)),
    WordForms("месяц", ("а",)),
    WordForms("год", ("а",)),
    WordForms("десятилети", ("я",)),
    WordForms("век", ("а",)),
    WordForms("столети", ("я",)),
    WordForms("тысячелети", ("я",)),
    WordForms("hour", PLURAL_SUFFIX),
    WordForms("day", PLURAL_SUFFIX),
    WordForms("week", PLURAL_SUFFIX),
    WordForms("month", PLURAL_SUFFIX),
    WordForms("year", PLURAL_SUFFIX),
    WordForms("decade", PLURAL_SUFFIX),
    WordForms("century"),
    WordForms("centuries"),
    WordForms("millennium"),
)
CLOCK_HOURS: tuple[WordForms, ...] = (
    WordForms("перв", ("ого",)),
    WordForms("втор", ("ого",)),
    WordForms("трет", ("ьего",)),
    WordForms("четверт", ("ого",)),
    WordForms("пят", ("ого",)),
    WordForms("шест", ("ого",)),
    WordForms("седьм", ("ого",)),
    WordForms("восьм", ("ого",)),
    WordForms("девят", ("ого",)),
    WordForms("десят", ("ого",)),
    WordForms("одиннадцат", ("ого",)),
    WordForms("двенадцат", ("ого",)),
    WordForms("past"),
)
SKIPPED_BEFORE_UNIT: tuple[str, ...] = ("a", "an", "the")
QUALIFIER_REQUIRED_FORMS: frozenset[str] = frozenset({"третью"})
SHARE_QUALIFIERS: frozenset[str] = frozenset(
    {
        "почти",
        "около",
        "примерно",
        "приблизительно",
        "порядка",
        "более",
        "менее",
        "больше",
        "меньше",
        "свыше",
        "лишь",
        "только",
        "всего",
        "ровно",
        "едва",
        "до",
    }
)
SHARE_SUM_MIN_MEMBERS = 3
SHARE_WHOLE = Fraction(1)
