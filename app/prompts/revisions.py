from app.domain.pipeline import PostAction

ANGLE_GUARD = (
    " The angle sets only the presentation and the order of the facts: every claim still "
    "comes only from the facts. If the facts do not fit this angle (for example, no person is "
    "named in them), take the closest presentation they allow and invent nothing."
)
ANGLES: tuple[str, ...] = (
    "Through a person: open with a person named in the facts and tell the facts through what "
    "this person did." + ANGLE_GUARD,
    "Through a detail or a number: open with the most concrete detail or number among the "
    "facts." + ANGLE_GUARD,
    "Through a place: open with the place where the events happened, as the facts name it."
    + ANGLE_GUARD,
    "Through a comparison of two facts from the list: put two facts side by side, without "
    "any conclusion about a causal link between them." + ANGLE_GUARD,
)

REVISION_INSTRUCTIONS: dict[PostAction, str] = {
    PostAction.SHORTER: (
        "Write a shorter version of the previous post: the same format and the same angle. "
        "Keep the main facts, drop secondary details and extra words. The format limits "
        "still apply."
    ),
    PostAction.THREAD: (
        "Rework the previous post into a thread. Keep its facts and its angle; you may add "
        "facts from the list where they carry the story."
    ),
    PostAction.ANGLE: (
        "Write the post again from the angle given above. The first sentence and the order "
        "of the facts must differ from the previous version."
    ),
    PostAction.VARIANT: (
        "Write another variant of the previous post: different wording, the same facts and "
        "the same angle."
    ),
}
