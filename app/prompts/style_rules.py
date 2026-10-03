from collections.abc import Iterable

from app.config.style import (
    BANNED_PHRASES,
    CAUTIOUS_WORDINGS,
    FORBIDDEN_DASHES,
    INVENTED_EXPERIENCE_PHRASES,
)

STYLE_RULES_TEMPLATE = (
    "Правила текста:\n"
    "1. Пиши по-русски, от первого лица автора («я»).\n"
    "2. Оценки и мнения от первого лица допустимы («Мне кажется, это была ошибка»), "
    "если это суждение, а не утверждение факта.\n"
    "3. Не выдумывай личный опыт. Автор ничего не видел своими глазами, никуда не ездил "
    "и ничему не был свидетелем. Запрещены обороты вроде {experience}.\n"
    "4. Никаких длинных и средних тире ({dashes}). Вместо них точка, запятая или "
    "двоеточие, как требует фраза. Дефис внутри слова («кто-то», «юго-запад») допустим.\n"
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
    "не согласны: {cautious}. Никогда не подавай их как установленный факт."
)
QUOTED_TEMPLATE = "«{text}»"
LIST_SEPARATOR = ", "
BANNED_SEPARATOR = "; "


def quoted(items: Iterable[str], separator: str) -> str:
    return separator.join(QUOTED_TEMPLATE.format(text=item) for item in items)


def render_style_rules() -> str:
    return STYLE_RULES_TEMPLATE.format(
        experience=quoted(INVENTED_EXPERIENCE_PHRASES, LIST_SEPARATOR),
        dashes=LIST_SEPARATOR.join(FORBIDDEN_DASHES),
        banned=quoted(BANNED_PHRASES, BANNED_SEPARATOR),
        cautious=quoted(CAUTIOUS_WORDINGS, LIST_SEPARATOR),
    )
