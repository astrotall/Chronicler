from collections.abc import Iterable

from app.config.style import (
    BANNED_PHRASES,
    CAUTIOUS_WORDINGS,
    DASH_EXAMPLE,
    DASH_REPLACEMENT,
    FILLER_CLOSER_EXAMPLES,
    FORBIDDEN_DASHES,
    INVENTED_EXPERIENCE_PHRASES,
    INVENTED_MEANING_EXAMPLE,
    NUMBER_DIGITS_EXAMPLE,
    OPINION_CLOSER_EXAMPLE,
    OPINION_MAX_PER_POST,
    SMALL_COUNT_IN_WORDS_EXAMPLE,
    RuleExample,
)

STYLE_RULES_TEMPLATE = (
    "Правила текста:\n"
    "1. Пиши по-русски. Если автор говорит о себе, то в первом лице («я»), но пост "
    "рассказывает о фактах, а не об авторе.\n"
    "2. Мнение от первого лица допустимо, но редко. Предел на пост или тред целиком: "
    "{opinion_max}, и пост вполне может обойтись без мнения. Мнение касается только того, "
    "что видно из фактов, и звучит как суждение, а не как факт («Мне кажется, это была "
    "ошибка»). Никогда не заканчивай мнением часть поста по привычке. Плохо: "
    "{opinion_closer}\n"
    "3. Не выдумывай личный опыт. Автор ничего не видел своими глазами, никуда не ездил "
    "и ничему не был свидетелем. Запрещены обороты вроде {experience}.\n"
    "4. Длинное и среднее тире ({dashes}) не используй. Где нужно тире, ставь дефис с "
    "пробелами ({dash_replacement}). Дефис с пробелами заменяет только тире: не ставь его "
    "вместо запятой, точки или двоеточия и не делай знаком препинания по умолчанию. Дефис "
    "внутри слова («кто-то», «по-петровски») и в диапазоне («1320-1330») допустим. "
    "{dash_example}\n"
    "5. Запрещённые обороты, в любой форме и в любом регистре: {banned}.\n"
    "6. Никаких троек: трёх прилагательных подряд, трёх параллельных фраз, трёх примеров "
    "подряд, трёх абзацев одной формы. Две детали или четыре допустимы, а одна острая деталь "
    "лучше любого перечня.\n"
    "7. Первое предложение цепляет конкретным: деталью, числом или парадоксом. Не "
    "приветствие, не анонс темы, не риторический вопрос, не «история знает много примеров».\n"
    "8. Длина предложений меняется: короткое после длинного. Несколько предложений одной "
    "длины подряд считаются дефектом.\n"
    "9. Без эмодзи.\n"
    "10. Без хештегов.\n"
    "11. Не заканчивай вопросом к читателю, если автор прямо не попросил об этом.\n"
    "12. Спорные сведения излагай осторожно, простой фразой с указанием, что источники "
    "не согласны: {cautious}. Никогда не подавай их как установленный факт.\n"
    "13. Не пиши предложений, единственная задача которых пересказать, прокомментировать "
    "или оценить предыдущее. Если предложение можно убрать и ничего не потеряется, убери "
    "его. Часть поста может закончиться самим фактом. Плохо: {fillers}\n"
    "14. Не добавляй выводов, причин, следствий и оценок значимости, которых нет в фактах. "
    "Выбирай факты, ставь их рядом и рассказывай живо, но не добавляй смысла: вывод "
    "читатель сделает сам. {meaning_example}\n"
    "15. Даты, годы, сроки, суммы, размеры, возрасты и проценты пиши цифрами, как в фактах: "
    "так их видит проверка чисел. Малые количества, от одного до десяти, в обычной речи "
    "можно словами ({small_count}). Не высчитывай промежутки, сроки и количества, которых "
    "нет в фактах. {number_example}\n"
    "16. Тред не строится по схеме «один факт на твит и завершающий оборот у каждого». "
    "Твит может быть одним простым предложением, связанные факты можно держать в одном "
    "твите. Но твит не обрывок: из него понятно, о ком и о чём речь."
)
QUOTED_TEMPLATE = "«{text}»"
RULE_EXAMPLE_TEMPLATE = "Плохо: «{bad}» Хорошо: «{good}»"
LIST_SEPARATOR = ", "
BANNED_SEPARATOR = "; "


def quoted(items: Iterable[str], separator: str) -> str:
    return separator.join(QUOTED_TEMPLATE.format(text=item) for item in items)


def rule_example(example: RuleExample) -> str:
    return RULE_EXAMPLE_TEMPLATE.format(bad=example.bad, good=example.good)


def render_style_rules() -> str:
    return STYLE_RULES_TEMPLATE.format(
        opinion_max=OPINION_MAX_PER_POST,
        opinion_closer=QUOTED_TEMPLATE.format(text=OPINION_CLOSER_EXAMPLE),
        experience=quoted(INVENTED_EXPERIENCE_PHRASES, LIST_SEPARATOR),
        dashes=LIST_SEPARATOR.join(FORBIDDEN_DASHES),
        dash_replacement=DASH_REPLACEMENT,
        dash_example=rule_example(DASH_EXAMPLE),
        banned=quoted(BANNED_PHRASES, BANNED_SEPARATOR),
        cautious=quoted(CAUTIOUS_WORDINGS, LIST_SEPARATOR),
        fillers=quoted(FILLER_CLOSER_EXAMPLES, LIST_SEPARATOR),
        meaning_example=rule_example(INVENTED_MEANING_EXAMPLE),
        small_count=QUOTED_TEMPLATE.format(text=SMALL_COUNT_IN_WORDS_EXAMPLE),
        number_example=rule_example(NUMBER_DIGITS_EXAMPLE),
    )
