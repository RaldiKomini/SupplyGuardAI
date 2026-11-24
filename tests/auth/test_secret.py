"""Tests for the signing-secret startup check."""

import pytest

from supplyguard.auth import require_auth_secret


@pytest.mark.parametrize(
    "environ", [{}, {"CHAINLIT_AUTH_SECRET": ""}, {"CHAINLIT_AUTH_SECRET": "short"}]
)
def test_missing_or_short_secret_is_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(RuntimeError, match="CHAINLIT_AUTH_SECRET"):
        require_auth_secret(environ)


def test_long_secret_is_accepted() -> None:
    secret = "x" * 32

    assert require_auth_secret({"CHAINLIT_AUTH_SECRET": secret}) == secret
