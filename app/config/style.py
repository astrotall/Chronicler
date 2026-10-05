from typing import NamedTuple

BANNED_PHRASES: tuple[str, ...] = (
    "это не просто X, а Y",
    "не просто X, а Y",
    "давайте разберёмся",
    "давайте погрузимся",
    "стоит отметить",
    "в заключение",
    "знали ли вы",
)
FORBIDDEN_DASHES: tuple[str, ...] = ("—", "–")
DASH_REPLACEMENT = " - "
INVENTED_EXPERIENCE_PHRASES: tuple[str, ...] = (
    "я видел",
    "я был там",
    "когда я стоял у этих стен",
    "мне довелось",
)
CAUTIOUS_WORDINGS: tuple[str, ...] = (
    "по одним данным ..., по другим ...",
    "источники расходятся",
)
OPINION_MAX_PER_POST = 1

PHRASE_PLACEHOLDERS: frozenset[str] = frozenset({"X", "Y"})
PHRASE_PLACEHOLDER_MAX_WORDS = 8
PHRASE_MIN_STEM_CHARS = 3
PHRASE_STEM_ENDINGS: tuple[str, ...] = (
    "аться",
    "иться",
    "ться",
    "емся",
    "имся",
    "ется",
    "ится",
    "ются",
    "ами",
    "ями",
    "ого",
    "его",
    "ому",
    "ему",
    "ыми",
    "ими",
    "ешь",
    "ете",
    "ите",
    "ала",
    "ало",
    "али",
    "ила",
    "ило",
    "или",
    "ась",
    "ось",
    "ись",
    "тся",
    "ах",
    "ях",
    "ой",
    "ей",
    "ий",
    "ый",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ую",
    "юю",
    "ом",
    "ем",
    "ам",
    "ям",
    "ов",
    "ев",
    "ет",
    "ит",
    "ут",
    "ют",
    "ат",
    "ят",
    "им",
    "ть",
    "те",
    "сь",
    "ся",
    "а",
    "я",
    "о",
    "е",
    "у",
    "ю",
    "ы",
    "и",
    "ь",
    "й",
)
BANNED_PHRASE_EXACT_WORDS: frozenset[str] = frozenset({"заключение"})
SENTENCE_END_CHARACTERS = ".!?…"
EMOJI_RANGES: tuple[tuple[str, str], ...] = (
    ("\U0001f000", "\U0001faff"),
    ("\u2600", "\u27bf"),
    ("\u2b50", "\u2b55"),
    ("\u231a", "\u231b"),
    ("\u23e9", "\u23fa"),
    ("\u3030", "\u3030"),
    ("\u303d", "\u303d"),
    ("\u3297", "\u3297"),
    ("\u3299", "\u3299"),
    ("\ufe0f", "\ufe0f"),
)
HASHTAG_MARK = "#"
CLOSING_QUESTION_MARK = "?"
DANGEROUS_STYLE_RULES: frozenset[str] = frozenset(
    {"unsupported_claim", "unverified_number", "ambiguous_reference", "invented_experience"}
)
DELETABLE_STYLE_RULES: frozenset[str] = frozenset({"unsupported_claim", "filler", "cliche"})
DANGLING_OPENERS: tuple[str, ...] = (
    "это",
    "этот",
    "эта",
    "эти",
    "тот",
    "так",
    "поэтому",
    "потому",
    "таким образом",
    "при этом",
    "однако",
)


class RuleExample(NamedTuple):
    bad: str
    good: str


OPINION_CLOSER_EXAMPLE = "Считаю, что прозвище здесь точнее любой летописной похвалы."
FILLER_CLOSER_EXAMPLES: tuple[str, ...] = (
    "Это деталь, которая держит внимание даже спустя столетия.",
    "Победа на Дону не отменила ни ордынской силы, ни будущих походов.",
)
INVENTED_MEANING_EXAMPLE = RuleExample(
    bad="Мамай ждал Ягайло, но тот не успел к битве. Союзник не пришёл, и это решило многое.",
    good="Мамай ждал Ягайло, но тот не успел к битве.",
)
DASH_EXAMPLE = RuleExample(
    bad="Победа — это начало.",
    good="Победа - это начало.",
)
NUMBER_DIGITS_EXAMPLE = RuleExample(
    bad="Через два года, в 1382 году, Тохтамыш сжёг Москву.",
    good="В 1382 году Тохтамыш сжёг Москву.",
)
SMALL_COUNT_IN_WORDS_EXAMPLE = "на поле сошлись два войска"
SHARE_WORD_EXAMPLES: tuple[str, ...] = ("треть", "половина", "вдвое", "каждый пятый")
SHARE_DIGITS_EXAMPLE = RuleExample(
    bad="Около трети депутатов поддержали закон, шестая часть была против, остальные воздержались.",
    good="Закон поддержали 37,4% депутатов, против было 14,3%.",
)
