import pytest
from app.config.constants import FACTS_DEFAULT_DOMAIN_GROUPS
from app.services.source_domain import source_domain


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a", "example.com"),
        ("https://www.example.com/a", "example.com"),
        ("https://WWW.Example.COM/a", "example.com"),
        ("https://example.com./a", "example.com"),
        ("https://news.example.com/a", "example.com"),
        ("https://a.b.example.com/a", "example.com"),
        ("http://example.com:8080/a", "example.com"),
        ("https://ru.wikipedia.org/wiki/X", "wikipedia.org"),
        ("https://en.wikipedia.org/wiki/X", "wikipedia.org"),
        ("https://en.m.wikipedia.org/wiki/X", "wikipedia.org"),
        ("https://commons.wikimedia.org/wiki/X", "wikipedia.org"),
        ("https://ruwiki.ru/wiki/X", "wikipedia.org"),
        ("https://www.wikiwand.com/ru/X", "wikipedia.org"),
        ("https://bbc.co.uk/a", "co.uk"),
    ],
)
def test_source_domain_with_default_groups(url: str, expected: str) -> None:
    assert source_domain(url, FACTS_DEFAULT_DOMAIN_GROUPS) == expected


def test_different_registrable_domains_are_independent() -> None:
    assert source_domain("https://example.com/a", ()) != source_domain("https://example.org/a", ())


def test_subdomain_and_parent_are_one_domain() -> None:
    assert source_domain("https://news.example.com/a", ()) == source_domain(
        "https://www.example.com/b", ()
    )


def test_without_groups_mirrors_are_independent() -> None:
    assert source_domain("https://ruwiki.ru/wiki/X", ()) == "ruwiki.ru"
    assert source_domain("https://ru.wikipedia.org/wiki/X", ()) == "wikipedia.org"


def test_a_custom_group_merges_its_members_and_their_subdomains() -> None:
    groups = (("britannica.com", "britannica.co.uk"),)

    assert source_domain("https://www.britannica.co.uk/a", groups) == "britannica.com"
    assert source_domain("https://kids.britannica.com/a", groups) == "britannica.com"
    assert source_domain("https://notbritannica.com/a", groups) == "notbritannica.com"
