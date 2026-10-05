from app.domain.draft import PostFormat
from app.domain.fact import ClaimStance, FactStatus
from app.domain.pipeline import FailureKind, PipelineStage, PostAction
from app.domain.style import StyleRule

START_TEXT = (
    "Я собираю факты по теме с источниками и пишу пост или тред для X. "
    "Пришли тему, а публикуешь ты сам."
)
INPUT_HINT_TEXT = (
    "Пришли тему текстом, например: «Куликовская битва». Формат можно задать в начале: "
    "«тред: Куликовская битва», «лонг: ...», «коротко: ...»."
)
TOPIC_EMPTY_TEXT = "После префикса нет темы. " + INPUT_HINT_TEXT
TOPIC_TOO_LONG_TEMPLATE = (
    "Сообщение длиннее {limit} символов. Это похоже на текст, а не на тему: пришли тему "
    "короче, одной фразой."
)
BUSY_TEXT = "Ещё работаю над предыдущим запросом. Пришли, когда закончу."
STALE_BUTTONS_TEXT = (
    "Эти кнопки устарели: бот перезапускался или вариант слишком старый. Пришли тему заново."
)
TIMEOUT_TEMPLATE = (
    "Не уложился в {seconds} с, запуск отменён. Попробуй ещё раз или сформулируй тему иначе."
)
INTERNAL_ERROR_TEXT = "Внутренняя ошибка, подробности в логе бота. Попробуй ещё раз."

TOPIC_FORMAT_PREFIXES: dict[str, PostFormat] = {
    "тред": PostFormat.THREAD,
    "лонг": PostFormat.LONG,
    "коротко": PostFormat.SHORT,
}
TOPIC_PREFIX_SEPARATOR = ":"

STAGE_TEXTS: dict[PipelineStage, str] = {
    PipelineStage.PLANNING: "Планирую поиск…",
    PipelineStage.RESEARCH: "Ищу источники…",
    PipelineStage.FACTS: "Выделяю факты…",
    PipelineStage.WRITING: "Пишу…",
    PipelineStage.STYLE: "Проверяю стиль…",
}
STAGE_NAMES: dict[PipelineStage, str] = {
    PipelineStage.PLANNING: "планирование поиска",
    PipelineStage.RESEARCH: "поиск источников",
    PipelineStage.FACTS: "выделение фактов",
    PipelineStage.WRITING: "написание",
    PipelineStage.STYLE: "проверка стиля",
}
FAILURE_REASONS: dict[FailureKind, str] = {
    FailureKind.CONFIG: "у шага не настроен ключ модели",
    FailureKind.AUTH: "модель не приняла ключ",
    FailureKind.REQUEST: "модель отклонила запрос",
    FailureKind.RATE_LIMIT: "превышен лимит запросов к модели",
    FailureKind.UNAVAILABLE: "модель недоступна",
    FailureKind.INVALID_RESPONSE: "модель вернула неверный ответ",
    FailureKind.OTHER: "ошибка модели",
}
STEP_FAILED_TEMPLATE = "Шаг «{stage}» не удался: {reason}. Попробуй ещё раз позже."

NO_SOURCES_TEXT = (
    "Не включён ни один источник. Задай WIKIPEDIA_CONTACT или TAVILY_API_KEY в .env и "
    "перезапусти бота."
)
RESEARCH_FAILED_TEMPLATE = "Источники не ответили: {failures}. Попробуй позже."
NOTHING_FOUND_TEXT = "Источники ничего не нашли по этой теме. Попробуй сформулировать иначе."
NOT_ENOUGH_FACTS_TEMPLATE = (
    "Фактов мало для поста: утверждаемых {assertable}, спорных {disputed}, версий и "
    "опровергнутых {attributed}, нужно не меньше {required} утверждаемых. Пост не пишу."
)
THREAD_UNAVAILABLE_TEMPLATE = (
    "Для треда нужно не меньше {required} утверждаемых фактов, есть {assertable}. Тред не делаю."
)

FAILURE_ITEM_TEMPLATE = "{source} ({kind})"
FAILURE_ITEM_COUNT_TEMPLATE = "{source} ({kind}) ×{count}"
FAILURE_SEPARATOR = ", "
FAILURES_NOTE_TEMPLATE = "Не ответили источники: {failures}."

ACTION_LABELS: dict[PostAction, str] = {
    PostAction.SHORTER: "короче",
    PostAction.THREAD: "в тред",
    PostAction.ANGLE: "другой заход",
    PostAction.VARIANT: "ещё вариант",
}

FACTS_HEADER_TEMPLATE = "<b>Факты</b>: в посте {used} из {total}"
VARIANT_FACTS_HEADER_TEMPLATE = "<b>Факты этого варианта</b>: {used}"
INSUFFICIENT_FACTS_HEADER_TEMPLATE = "<b>Найденные факты</b>: {total}"
USED_SECTION = "<b>В посте</b>"
UNUSED_SECTION = "<b>Не вошли в пост</b>"
OTHER_FACTS_TEMPLATE = "Остальные факты ({count}) в первом ответе."
FACT_HEADER_TEMPLATE = "<b>{fact_id}</b> · {status}"
DISPUTE_REASON_TEMPLATE = "Почему спорно (вместе с {others}): {explanation}"
UNGROUPED_DISPUTE_REASON_LINE = "Почему спорно: источники расходятся"
ID_SEPARATOR = ", "
LINK_TEMPLATE = '<a href="{url}">{label}</a>'
WEAK_LINK_TEMPLATE = '<a href="{url}">{label}</a> · слабый'
STATUS_LABELS: dict[FactStatus, str] = {
    FactStatus.CONFIRMED: "подтверждён: разные домены",
    FactStatus.SINGLE: "один источник",
    FactStatus.DISPUTED: "СПОРНО",
}
DISPUTED_STATUS_LABEL = STATUS_LABELS[FactStatus.DISPUTED]
STANCE_LABELS: dict[ClaimStance, str] = {
    ClaimStance.CLAIMED: "версия",
    ClaimStance.REBUTTED: "опровергнуто",
}
STANCE_LABEL_TEMPLATE = "{status} · {stance}"
REBUTTED_BY_TEMPLATE = "Опровергается: {ids}"
REBUTTED_WITHOUT_REBUTTAL_LINE = "Опровергается в источнике, опровержение не прошло проверку"
REBUTS_TEMPLATE = "Опровергает: {ids}"

WARNINGS_HEADER = "Предупреждения:"
WARNING_LINE_TEMPLATE = "- {text}"
WEAK_FACTS_TEMPLATE = (
    "{weak} из {total} фактов поста опираются только на слабые источники (они помечены в "
    "списке фактов). Проверь их перед публикацией."
)
ATTRIBUTED_USED_TEMPLATE = (
    "В посте есть версии или опровергнутые утверждения ({ids}): проверь, что они поданы "
    "с оговоркой, а не как факт."
)
UNVERIFIED_NUMBERS_TEMPLATE = "Числа, которых нет в фактах: {numbers}. Проверь их или убери."
NUMBER_SEPARATOR = ", "
LENGTH_PART_TEMPLATE = "{part}: {actual} символов при лимите {limit}."
LENGTH_TOO_MANY_PARTS_TEMPLATE = "в треде {actual} твитов при максимуме {limit}."
LENGTH_TOO_SHORT_TEMPLATE = "{part}: {actual} символов при минимуме {limit}, слишком коротко."
LENGTH_TOO_FEW_FACTS_TEMPLATE = "в посте {actual} фактов при минимуме {limit}."
LENGTH_TOO_FEW_PARTS_TEMPLATE = "в треде {actual} твитов при минимуме {limit}."
LENGTH_THREAD_TOO_FEW_FACTS_TEMPLATE = "использовано фактов {actual} при минимуме {limit}."
LENGTH_PREFIX = "Длина, "
POST_PART_NAME = "пост"
TWEET_PART_TEMPLATE = "твит {part}"
VIOLATION_TEMPLATE = "{rule}{where}: {explanation}"
VIOLATION_EXCERPT_TEMPLATE = " «{excerpt}»"
VIOLATION_PART_TEMPLATE = " ({part})"
CRITIC_FAILED_TEXT = (
    "Критик не проверил этот текст: модель не ответила. Проверены только правила кода."
)
REGENERATION_FAILED_TEXT = (
    "Перегенерация после замечаний не удалась, показан лучший из уже готовых вариантов."
)
REGRESSIONS_REJECTED_TEMPLATE = (
    "Правок фильтра стиля отклонено: {count}. Они слишком сильно сокращали текст, "
    "показан предыдущий вариант."
)
REMOVED_CLAIMS_TEMPLATE = (
    "Удалено предложений: {count}. Критик отметил их как утверждения без опоры в фактах или "
    "как пустые фразы, поэтому код вырезал их из текста. Список использованных фактов мог "
    "остаться прежним."
)
UNREPORTED_SURVIVORS_TEMPLATE = (
    "Предложений, которые критик отмечал раньше и которые остались в тексте без изменений, "
    "а последняя проверка их не отметила: {count}."
)
DROPPED_TAIL_TEMPLATE = (
    "Код срезал конец поста, чтобы уложиться в лимит: {pieces}. Список использованных "
    "фактов мог стать неточным: он описывает полный текст."
)
DROPPED_PIECE_TEMPLATE = "«{piece}»"
DROPPED_PIECE_SEPARATOR = " "
THREAD_DOWNGRADE_TEMPLATE = (
    "Для треда нужно не меньше {required} утверждаемых фактов, нашлось {assertable}. "
    "Сделал короткий пост."
)
RULE_LABELS: dict[StyleRule, str] = {
    StyleRule.DASH: "Длинное тире",
    StyleRule.BANNED_PHRASE: "Запрещённый оборот",
    StyleRule.INVENTED_EXPERIENCE: "Выдуманный личный опыт",
    StyleRule.EMOJI: "Эмодзи",
    StyleRule.HASHTAG: "Хештег",
    StyleRule.CLOSING_QUESTION: "Вопрос в конце",
    StyleRule.LENGTH: "Длина",
    StyleRule.UNVERIFIED_NUMBER: "Число не из фактов",
    StyleRule.CLICHE: "Штамп",
    StyleRule.TRIPLET: "Тройка",
    StyleRule.FILLER: "Пустая фраза",
    StyleRule.OPINION: "Мнение",
    StyleRule.UNSUPPORTED_CLAIM: "Утверждение не из фактов",
    StyleRule.AMBIGUOUS_REFERENCE: "Двусмысленная ссылка",
}
