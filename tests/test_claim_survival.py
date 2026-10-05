import pytest
from app.config.constants import STYLE_DEFAULT_FRAGMENT_OVERLAP
from app.domain.style import StyleRule, Violation, ViolationSource
from app.services.claim_survival import (
    Survival,
    content_stems,
    exact_part,
    find_survivors,
    same_fragment,
    score_fragment,
    sentence_of,
)

THRESHOLD = STYLE_DEFAULT_FRAGMENT_OVERLAP
FLAGGED = "Тот же график привёл к росту брака на заводах и падению выпуска продукции."
REWORDED = "Тот же график вёл к росту брака на заводах и падению выпуска продукции."
PARAPHRASE = "Завод работал по новому графику."
OTHER = "8 сентября 1380 года на Куликовом поле сошлись войска."
FILLER = "Это решило многое."


def flag(excerpt: str, rule: StyleRule = StyleRule.UNSUPPORTED_CLAIM, part: int = 1) -> Violation:
    return Violation(
        rule=rule,
        source=ViolationSource.CRITIC,
        part=part,
        excerpt=excerpt,
        explanation="Пояснение.",
    )


def test_stems_cut_endings_and_keep_numbers_and_drop_short_words() -> None:
    stems = content_stems("Тот же график привёл к росту брака в 1935 году.")

    assert stems == {"графи", "приве", "росту", "брака", "1935", "году"}


def test_a_verbatim_fragment_is_found_in_the_part_that_holds_it() -> None:
    assert exact_part(FLAGGED, [OTHER, f"Второй твит. {FLAGGED}"]) == 2


def test_the_exact_match_ignores_case_quotes_dashes_and_yo() -> None:
    assert (
        exact_part("«ТОТ ЖЕ график привёл к росту брака на заводах»", [FLAGGED.replace("ё", "е")])
        == 1
    )


def test_a_fragment_inside_another_word_is_not_a_match() -> None:
    assert exact_part("уже", ["Мы дружески поговорили."]) is None


def test_an_empty_fragment_is_never_a_match() -> None:
    assert exact_part("«»", [FLAGGED]) is None


def test_a_reworded_copy_with_the_same_added_words_is_a_near_survivor() -> None:
    [survivor] = find_survivors([flag(FLAGGED)], [OTHER, REWORDED], THRESHOLD)

    assert survivor.verdict is Survival.NEAR
    assert survivor.violation.excerpt == REWORDED
    assert survivor.violation.part == 2


def test_a_genuine_paraphrase_below_the_threshold_is_not_a_survivor() -> None:
    assert find_survivors([flag(FLAGGED)], [OTHER, PARAPHRASE], THRESHOLD) == []


def test_a_fragment_moved_to_another_tweet_is_still_found() -> None:
    texts = ["Первый твит.", "Второй твит о другом.", f"Третий твит. {REWORDED}"]

    [survivor] = find_survivors([flag(FLAGGED, part=1)], texts, THRESHOLD)

    assert survivor.violation.part == 3
    assert survivor.verdict is Survival.NEAR


def test_a_verbatim_fragment_moved_to_another_tweet_is_exact() -> None:
    [survivor] = find_survivors([flag(FLAGGED, part=1)], [OTHER, "Другое.", FLAGGED], THRESHOLD)

    assert survivor.verdict is Survival.EXACT
    assert survivor.violation.part == 3
    assert survivor.violation.excerpt == FLAGGED


def test_a_short_fragment_counts_only_when_it_is_verbatim() -> None:
    assert find_survivors([flag(FILLER)], [OTHER, "Это решило много."], THRESHOLD) == []
    [survivor] = find_survivors([flag(FILLER)], [OTHER, f"Затем {FILLER}"], THRESHOLD)
    assert survivor.verdict is Survival.EXACT


def test_the_threshold_decides_a_near_copy() -> None:
    half = "Тот же график вызвал падение выпуска продукции и брак."

    assert find_survivors([flag(FLAGGED)], [half], 0.3) != []
    assert find_survivors([flag(FLAGGED)], [half], 0.9) == []


def test_empty_new_text_has_no_survivors() -> None:
    assert find_survivors([flag(FLAGGED)], [], THRESHOLD) == []


def test_a_finding_without_an_excerpt_is_skipped() -> None:
    violation = Violation(
        rule=StyleRule.UNSUPPORTED_CLAIM, source=ViolationSource.CRITIC, explanation="Пояснение."
    )

    assert find_survivors([violation], [FLAGGED], THRESHOLD) == []


def test_two_findings_on_one_sentence_give_one_survivor() -> None:
    flagged = [flag(FLAGGED), flag(FLAGGED, StyleRule.FILLER)]

    assert len(find_survivors(flagged, [REWORDED], THRESHOLD)) == 1


@pytest.mark.parametrize(
    ("new_text", "stems", "verdict"),
    [
        (FLAGGED, 8, Survival.EXACT),
        (REWORDED, 8, Survival.NEAR),
        (PARAPHRASE, 8, Survival.GONE),
    ],
)
def test_the_score_reports_stems_score_and_verdict(
    new_text: str, stems: int, verdict: Survival
) -> None:
    result = score_fragment(FLAGGED, [new_text], THRESHOLD)

    assert result.stems == stems
    assert result.verdict is verdict
    assert 0 <= result.score <= 1


def test_the_score_of_nothing_is_zero() -> None:
    assert score_fragment(FLAGGED, [], THRESHOLD).score == 0.0


def test_the_same_fragment_is_found_by_containment_in_either_direction() -> None:
    assert same_fragment("привёл к росту брака", FLAGGED, THRESHOLD)
    assert same_fragment(FLAGGED, "привёл к росту брака", THRESHOLD)


def test_the_same_fragment_is_found_by_near_overlap() -> None:
    assert same_fragment(FLAGGED, REWORDED, THRESHOLD)
    assert not same_fragment(FLAGGED, PARAPHRASE, THRESHOLD)
    assert not same_fragment(FLAGGED, OTHER, THRESHOLD)


def test_a_sentence_is_found_by_its_excerpt() -> None:
    text = f"Сошлись полки. {FLAGGED} Затем начался бой."

    assert sentence_of("росту брака", text) == FLAGGED
    assert sentence_of("нет такого", text) == "нет такого"
