import pytest
from app.domain.snippet import Snippet, SnippetOrigin, normalize_url, snippet_id, url_host
from pydantic import ValidationError

URL = "https://example.org/history/mamai"


def build(**overrides: str) -> Snippet:
    values = {"origin": SnippetOrigin.TAVILY, "title": "Мамай", "url": URL, "text": "Текст"}
    return Snippet.model_validate({**values, **overrides})


def test_id_is_stable_between_instances() -> None:
    assert build().id == build().id
    assert build().id == snippet_id(URL)
    assert len(build().id) == 16


def test_id_ignores_text_and_origin() -> None:
    assert build(text="Другой текст").id == build().id
    assert build(origin=SnippetOrigin.WIKIPEDIA_RU).id == build().id


def test_id_follows_url_normalisation() -> None:
    assert build(url="HTTPS://Example.ORG/history/mamai/#intro").id == build().id
    assert build(url="https://example.org/history/other").id != build().id


def test_given_id_is_replaced_by_the_derived_one() -> None:
    assert build(id="forged").id == build().id


@pytest.mark.parametrize("url", ["", "   ", "example.org/page", "ftp://example.org/a", "https://"])
def test_url_must_be_an_absolute_web_url(url: str) -> None:
    with pytest.raises(ValidationError):
        build(url=url)


def test_url_is_required() -> None:
    with pytest.raises(ValidationError):
        Snippet.model_validate({"origin": "tavily", "title": "t", "text": "x"})


def test_origin_is_required_and_restricted() -> None:
    with pytest.raises(ValidationError):
        Snippet.model_validate({"title": "t", "url": URL, "text": "x"})
    with pytest.raises(ValidationError):
        build(origin="google")


@pytest.mark.parametrize("text", ["", "  \n\t "])
def test_blank_text_is_rejected(text: str) -> None:
    with pytest.raises(ValidationError):
        build(text=text)


def test_text_and_url_are_stripped() -> None:
    snippet = build(text="  Текст \n", url=f" {URL} ")

    assert snippet.text == "Текст"
    assert snippet.url == URL


def test_lang_is_optional() -> None:
    assert build().lang is None
    assert build(lang="ru").lang == "ru"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.ORG/a/b/", "https://example.org/a/b"),
        ("https://example.org/a#section", "https://example.org/a"),
        ("https://example.org/", "https://example.org"),
        ("https://example.org", "https://example.org"),
        ("https://example.org/a?x=1&y=2#f", "https://example.org/a?x=1&y=2"),
        ("HTTP://example.org/A", "http://example.org/A"),
        ("  https://example.org/a  ", "https://example.org/a"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Sub.Example.org/a", "sub.example.org"),
        ("https://example.org:8080/a", "example.org"),
    ],
)
def test_url_host(raw: str, expected: str) -> None:
    assert url_host(raw) == expected
