import pytest
from app.config.style import DANGLING_OPENERS
from app.services.claim_removal import (
    RemovalGuard,
    Span,
    cut_span,
    opens_dangling,
    remove_flagged_sentences,
    sentence_spans,
)

FLAGGED = "Тот же график привёл к росту брака на заводах и падению выпуска продукции."
KEEP_A = "Сошлись русские полки."
KEEP_B = "Затем начался бой."
FREE = 100
NO_LIMIT = RemovalGuard(min_parts=1, min_chars=0, keeps_enough=lambda previous, current: True)


def guard(
    min_parts: int = 1, min_chars: int = 0, ratio: float = 0.6, free: int = FREE
) -> RemovalGuard:
    return RemovalGuard(
        min_parts=min_parts,
        min_chars=min_chars,
        keeps_enough=lambda previous, current: (
            not (current < previous * ratio and previous - current > free)
        ),
    )


def cut(text: str, sentence: str) -> str:
    start = text.index(sentence)
    return cut_span(text, Span(start, start + len(sentence)))


def test_sentences_have_exact_spans() -> None:
    text = f"{KEEP_A} {KEEP_B}"

    assert [text[span.start : span.end] for span in sentence_spans(text)] == [KEEP_A, KEEP_B]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"{KEEP_A} {FLAGGED} {KEEP_B}", f"{KEEP_A} {KEEP_B}"),
        (f"{FLAGGED} {KEEP_B}", KEEP_B),
        (f"{KEEP_A} {FLAGGED}", KEEP_A),
        (
            f"{KEEP_A}\n\n{FLAGGED} {KEEP_B}\n\nТретий абзац.",
            f"{KEEP_A}\n\n{KEEP_B}\n\nТретий абзац.",
        ),
        (f"{KEEP_A} {FLAGGED}\n\nТретий абзац.", f"{KEEP_A}\n\nТретий абзац."),
        (f"{KEEP_A}\n\n{FLAGGED}\n\nТретий абзац.", f"{KEEP_A}\n\nТретий абзац."),
        (f"{KEEP_A}\n\n{FLAGGED}", KEEP_A),
        (FLAGGED, ""),
    ],
)
def test_a_cut_keeps_the_spacing_and_the_paragraph_breaks(text: str, expected: str) -> None:
    assert cut(text, FLAGGED) == expected


def test_a_flagged_sentence_is_removed_and_recorded() -> None:
    text = f"{KEEP_A} {FLAGGED} {KEEP_B}"

    removal = remove_flagged_sentences([text], [FLAGGED], NO_LIMIT)

    assert removal.texts == [f"{KEEP_A} {KEEP_B}"]
    assert removal.removed == [FLAGGED]


def test_a_phrase_inside_a_sentence_is_never_cut_out() -> None:
    text = f"{KEEP_A} {FLAGGED} {KEEP_B}"

    removal = remove_flagged_sentences([text], ["привёл к росту брака"], NO_LIMIT)

    assert removal.texts == [text]
    assert removal.removed == []


def test_an_excerpt_that_is_almost_the_whole_sentence_removes_it() -> None:
    text = f"{KEEP_A} {FLAGGED}"
    excerpt = "Тот же график привёл к росту брака на заводах и падению выпуска"

    assert remove_flagged_sentences([text], [excerpt], NO_LIMIT).removed == [FLAGGED]


def test_an_excerpt_spanning_two_whole_sentences_removes_both() -> None:
    text = f"{KEEP_A} {FLAGGED} Это решило многое. {KEEP_B}"

    removal = remove_flagged_sentences([text], [f"{FLAGGED} Это решило многое."], NO_LIMIT)

    assert removal.texts == [f"{KEEP_A} {KEEP_B}"]
    assert len(removal.removed) == 2


def test_a_missing_excerpt_removes_nothing() -> None:
    removal = remove_flagged_sentences([KEEP_A], [FLAGGED], NO_LIMIT)

    assert removal.texts == [KEEP_A]
    assert removal.removed == []


def test_a_whole_tweet_goes_only_above_the_tweet_minimum() -> None:
    tweets = [KEEP_A, KEEP_B, FLAGGED]

    assert remove_flagged_sentences(tweets, [FLAGGED], guard(min_parts=2)).texts == [KEEP_A, KEEP_B]
    blocked = remove_flagged_sentences(tweets, [FLAGGED], guard(min_parts=3))
    assert blocked.texts == tweets
    assert blocked.removed == []


def test_the_only_part_is_never_emptied() -> None:
    assert remove_flagged_sentences([FLAGGED], [FLAGGED], guard(min_parts=1)).texts == [FLAGGED]


def test_a_sentence_inside_a_tweet_keeps_the_tweet_count() -> None:
    tweets = [f"{KEEP_A} {FLAGGED}", KEEP_B]

    assert remove_flagged_sentences(tweets, [FLAGGED], guard(min_parts=2)).texts == [KEEP_A, KEEP_B]


@pytest.mark.parametrize(
    ("min_chars", "removed"), [(len(KEEP_A) + len(KEEP_B), True), (200, False)]
)
def test_a_text_is_not_cut_below_the_character_minimum(min_chars: int, removed: bool) -> None:
    text = f"{KEEP_A} {KEEP_B} {FLAGGED}"

    removal = remove_flagged_sentences([text], [FLAGGED], guard(min_chars=min_chars))

    assert bool(removal.removed) is removed


def test_the_regression_tolerance_caps_the_total_loss() -> None:
    body = "а" * 200 + "."
    text = f"{body} {FLAGGED}"

    assert remove_flagged_sentences([text], [FLAGGED], guard()).removed == [FLAGGED]
    big = "б" * 120 + "."
    text = f"{big} {FLAGGED} {big}"
    assert remove_flagged_sentences([text], [FLAGGED], guard(ratio=0.9, free=10)).removed == []


def test_the_cap_is_counted_against_the_original_total_across_sentences() -> None:
    first = "Первое лишнее утверждение о заводе и графике работы цехов."
    second = "Второе лишнее утверждение о заводе и графике работы цехов."
    text = f"{'в' * 60}. {first} {second}"

    removal = remove_flagged_sentences([text], [first, second], guard(ratio=0.9, free=70))

    assert removal.removed == [first]


@pytest.mark.parametrize("opener", DANGLING_OPENERS)
def test_every_configured_opener_marks_a_dangling_sentence(opener: str) -> None:
    assert opens_dangling(f"{opener.capitalize()} решило исход битвы.")
    assert opens_dangling(f"«{opener.upper()}», сказал он.")


@pytest.mark.parametrize(
    "sentence",
    ["Затем начался бой.", "Этажи были высокими.", "Тоже верно.", "Битва, однако, закончилась."],
)
def test_other_sentences_do_not_open_dangling(sentence: str) -> None:
    assert not opens_dangling(sentence)


def test_a_sentence_followed_by_a_dangling_one_is_kept_and_counted() -> None:
    text = f"{KEEP_A} {FLAGGED} Это решило многое."

    removal = remove_flagged_sentences([text], [FLAGGED], NO_LIMIT)

    assert removal.texts == [text]
    assert removal.removed == []
    assert removal.blocked == 1


def test_a_sentence_followed_by_a_plain_one_is_removed() -> None:
    removal = remove_flagged_sentences([f"{FLAGGED} {KEEP_B}"], [FLAGGED], NO_LIMIT)

    assert removal.texts == [KEEP_B]
    assert removal.blocked == 0


def test_the_last_sentence_of_a_tweet_checks_the_start_of_the_next_tweet() -> None:
    blocked = remove_flagged_sentences(
        [f"{KEEP_A} {FLAGGED}", "Однако бой продолжался."], [FLAGGED], NO_LIMIT
    )
    allowed = remove_flagged_sentences(
        [f"{KEEP_A} {FLAGGED}", "Бой продолжался."], [FLAGGED], NO_LIMIT
    )

    assert blocked.removed == []
    assert blocked.blocked == 1
    assert allowed.texts == [KEEP_A, "Бой продолжался."]


def test_a_whole_tweet_followed_by_a_dangling_tweet_is_kept() -> None:
    removal = remove_flagged_sentences(
        [KEEP_A, FLAGGED, "Поэтому всё изменилось."], [FLAGGED], NO_LIMIT
    )

    assert removal.removed == []
    assert removal.blocked == 1


def test_the_last_sentence_of_the_post_has_nothing_to_dangle_after_it() -> None:
    removal = remove_flagged_sentences([KEEP_A, FLAGGED], [FLAGGED], NO_LIMIT)

    assert removal.texts == [KEEP_A]


def test_an_adjacent_flagged_pair_checks_the_sentence_after_both() -> None:
    text = f"{FLAGGED} Это решило многое. Это было важно."

    removal = remove_flagged_sentences([text], [f"{FLAGGED} Это решило многое."], NO_LIMIT)

    assert removal.removed == []
    assert removal.blocked == 1


def test_a_blocked_sentence_does_not_stop_the_next_flagged_one() -> None:
    other = "Эпоха сделала свой выбор окончательно и бесповоротно навсегда."
    text = f"{FLAGGED} Это решило многое. {KEEP_A} {other} {KEEP_B}"

    removal = remove_flagged_sentences([text], [FLAGGED, other], NO_LIMIT)

    assert removal.blocked == 1
    assert removal.removed == [other]
