from collections.abc import Sequence

from app.config.constants import DOMAIN_LABEL_SEPARATOR, REGISTRABLE_DOMAIN_LABELS, WWW_PREFIX
from app.domain.snippet import url_host
from app.services.research import host_matches


def normalize_host(host: str) -> str:
    stripped = host.strip().lower().rstrip(DOMAIN_LABEL_SEPARATOR)
    return stripped.removeprefix(WWW_PREFIX)


def source_domain(url: str, groups: Sequence[Sequence[str]]) -> str:
    host = normalize_host(url_host(url))
    for group in groups:
        if group and host_matches(host, group):
            return group[0]
    labels = host.split(DOMAIN_LABEL_SEPARATOR)
    return DOMAIN_LABEL_SEPARATOR.join(labels[-REGISTRABLE_DOMAIN_LABELS:])


def is_weak_source(url: str, weak_domains: Sequence[str]) -> bool:
    return host_matches(normalize_host(url_host(url)), [normalize_host(d) for d in weak_domains])
