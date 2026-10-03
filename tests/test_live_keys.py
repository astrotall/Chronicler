import pytest
from pydantic import SecretStr

from live_keys import has_live_key


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (None, False),
        (SecretStr(""), False),
        (SecretStr("  \t\n"), False),
        (SecretStr("sk-live-key"), True),
    ],
    ids=["none", "empty", "whitespace", "set"],
)
def test_has_live_key(key: SecretStr | None, expected: bool) -> None:
    assert has_live_key(key) is expected
