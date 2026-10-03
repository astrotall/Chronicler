from typing import NamedTuple

BANNED_PHRASES: tuple[str, ...] = (
    "это не просто X, а Y",
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
