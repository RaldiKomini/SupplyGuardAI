"""Tests for authentication and role-to-scope mapping."""

import csv
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from supplyguard.auth import AccessRegistry, load_access_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "access.toml"
USERS_CSV = ROOT / "support-data" / "config" / "users.csv"
PASSWORDS = {user.password_env: f"pw-{user.id}" for user in load_access_config(CONFIG_PATH).users}
BASE_SCOPES = ("internal", "procurement", "public")


@pytest.fixture
def registry() -> AccessRegistry:
    return AccessRegistry(load_access_config(CONFIG_PATH), environ=PASSWORDS)


@pytest.mark.parametrize(
    ("user_id", "role", "scopes", "factories"),
    [
        ("u-pm-01", "procurement_manager", BASE_SCOPES, ("F1", "F2", "F3")),
        ("u-an-01", "operations_analyst", BASE_SCOPES, ("F1",)),
        ("u-ic-01", "incident_coordinator", BASE_SCOPES, ("F2", "F3")),
        (
            "u-fc-01",
            "finance_controller",
            ("finance-confidential", *BASE_SCOPES),
            ("F1", "F2", "F3"),
        ),
        ("u-ro-01", "viewer", ("public",), ()),
    ],
)
def test_each_user_receives_role_scopes_and_factories(
    registry: AccessRegistry,
    user_id: str,
    role: str,
    scopes: tuple[str, ...],
    factories: tuple[str, ...],
) -> None:
    context = registry.authenticate(user_id, f"pw-{user_id}")

    assert context is not None
    assert context.role == role
    assert context.allowed_scopes == scopes
    assert context.factory_ids == factories


def test_only_finance_controller_receives_finance_confidential(registry: AccessRegistry) -> None:
    for user in load_access_config(CONFIG_PATH).users:
        context = registry.authorization_for(user.id)
        assert context is not None
        has_scope = "finance-confidential" in context.allowed_scopes
        assert has_scope == (user.role == "finance_controller")


def test_access_config_matches_the_supplied_users_csv(registry: AccessRegistry) -> None:
    with USERS_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    config = load_access_config(CONFIG_PATH)

    assert {user.id for user in config.users} == {row["user_id"] for row in rows}
    for row in rows:
        context = registry.authorization_for(row["user_id"])
        assert context is not None
        assert context.role == row["role"]
        assert set(context.allowed_scopes) == set(row["document_scopes"].split(";"))
        assert set(context.factory_ids) == set(filter(None, row["factory_access"].split(";")))


def test_env_example_ships_no_credentials() -> None:
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if re.match(r"(AUTH_PASSWORD_|CHAINLIT_AUTH_SECRET)", line)
    )
    expected = {user.password_env for user in load_access_config(CONFIG_PATH).users}

    assert expected | {"CHAINLIT_AUTH_SECRET"} == set(values)
    assert all(value == "" for value in values.values())


def test_compose_passes_every_account_password_to_the_app() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    for user in load_access_config(CONFIG_PATH).users:
        assert f"{user.password_env}: ${{{user.password_env}:-}}" in compose


def test_wrong_password_is_rejected(registry: AccessRegistry) -> None:
    assert registry.authenticate("u-fc-01", "pw-u-an-01") is None


def test_unknown_user_is_rejected(registry: AccessRegistry) -> None:
    assert registry.authenticate("u-nobody", "pw-u-fc-01") is None
    assert registry.authenticate("u-nobody", "") is None


@pytest.mark.parametrize("environ", [{}, {"AUTH_PASSWORD_U_FC_01": ""}])
def test_user_without_configured_password_cannot_sign_in(environ: dict[str, str]) -> None:
    registry = AccessRegistry(load_access_config(CONFIG_PATH), environ=environ)

    assert registry.authenticate("u-fc-01", "") is None


def test_authorization_for_unknown_user_is_none(registry: AccessRegistry) -> None:
    assert registry.authorization_for("u-nobody") is None


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
