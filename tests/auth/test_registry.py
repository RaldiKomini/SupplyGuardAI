"""Tests for authentication and role-to-scope mapping."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from supplyguard.auth import AccessRegistry, load_access_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "access.toml"
# Scope names the retrieval layer and the supplied data use.
KNOWN_SCOPES = {"public", "internal", "procurement", "finance-confidential"}
PASSWORDS = {
    "AUTH_PASSWORD_U_RO_01": "pw-ro",
    "AUTH_PASSWORD_U_PM_01": "pw-pm",
    "AUTH_PASSWORD_U_FIN_01": "pw-fin",
}


@pytest.fixture
def registry() -> AccessRegistry:
    return AccessRegistry(load_access_config(CONFIG_PATH), environ=PASSWORDS)


@pytest.mark.parametrize(
    ("user_id", "password", "role", "scopes"),
    [
        ("u-ro-01", "pw-ro", "viewer", ("public",)),
        (
            "u-pm-01",
            "pw-pm",
            "procurement_manager",
            ("internal", "procurement", "public"),
        ),
        (
            "u-fin-01",
            "pw-fin",
            "finance_controller",
            ("finance-confidential", "internal", "procurement", "public"),
        ),
    ],
)
def test_each_role_receives_its_scopes(
    registry: AccessRegistry, user_id: str, password: str, role: str, scopes: tuple[str, ...]
) -> None:
    context = registry.authenticate(user_id, password)

    assert context is not None
    assert context.role == role
    assert context.allowed_scopes == scopes


def test_only_finance_controller_receives_finance_confidential(registry: AccessRegistry) -> None:
    for user in load_access_config(CONFIG_PATH).users:
        context = registry.authorization_for(user.id)
        assert context is not None
        has_scope = "finance-confidential" in context.allowed_scopes
        assert has_scope == (user.role == "finance_controller")


def test_configured_scopes_and_factories_match_the_supplied_data_vocabulary() -> None:
    config = load_access_config(CONFIG_PATH)

    for role in config.roles.values():
        assert set(role.scopes) <= KNOWN_SCOPES
    for user in config.users:
        assert all(re.fullmatch(r"F\d+", factory) for factory in user.factory_ids)


def test_env_example_ships_no_credentials() -> None:
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if re.match(r"(AUTH_PASSWORD_|CHAINLIT_AUTH_SECRET)", line)
    )

    assert values
    assert all(value == "" for value in values.values())


def test_wrong_password_is_rejected(registry: AccessRegistry) -> None:
    assert registry.authenticate("u-fin-01", "pw-ops") is None


def test_unknown_user_is_rejected(registry: AccessRegistry) -> None:
    assert registry.authenticate("u-nobody", "pw-fin") is None
    assert registry.authenticate("u-nobody", "") is None


@pytest.mark.parametrize("environ", [{}, {"AUTH_PASSWORD_U_FIN_01": ""}])
def test_user_without_configured_password_cannot_sign_in(environ: dict[str, str]) -> None:
    registry = AccessRegistry(load_access_config(CONFIG_PATH), environ=environ)

    assert registry.authenticate("u-fin-01", "") is None


def test_authorization_for_unknown_user_is_none(registry: AccessRegistry) -> None:
    assert registry.authorization_for("u-nobody") is None


def test_authorization_includes_factories(registry: AccessRegistry) -> None:
    context = registry.authorization_for("u-pm-01")

    assert context is not None
    assert context.factory_ids == ("F1", "F2", "F3")


def test_committed_config_contains_no_credentials() -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8").lower()

    assert "password =" not in text
    assert "secret" not in text


def test_config_rejects_unknown_role(tmp_path: Path) -> None:
    path = tmp_path / "access.toml"
    path.write_text(
        '[roles.viewer]\nscopes = ["public"]\n\n[[users]]\n'
        'id = "u-1"\ndisplay_name = "A"\nrole = "admin"\npassword_env = "P"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unknown role"):
        load_access_config(path)


def test_config_rejects_duplicate_user_ids(tmp_path: Path) -> None:
    user = 'id = "u-1"\ndisplay_name = "A"\nrole = "viewer"\npassword_env = "P"\n'
    path = tmp_path / "access.toml"
    path.write_text(
        f'[roles.viewer]\nscopes = ["public"]\n\n[[users]]\n{user}\n[[users]]\n{user}',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unique"):
        load_access_config(path)
