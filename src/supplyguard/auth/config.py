"""Typed access configuration loaded from TOML."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoleConfig(_Config):
    scopes: tuple[str, ...] = ()


class UserConfig(_Config):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field(min_length=1)
    factory_ids: tuple[str, ...] = ()
    password_env: str = Field(min_length=1)


class AccessConfig(_Config):
    roles: dict[str, RoleConfig]
    users: tuple[UserConfig, ...]

    @model_validator(mode="after")
    def users_are_unique_and_have_known_roles(self) -> "AccessConfig":
        ids = [user.id for user in self.users]
        if len(ids) != len(set(ids)):
            raise ValueError("user ids must be unique")
        emails = [user.email.casefold() for user in self.users if user.email]
        if len(emails) != len(set(emails)) or set(emails) & {u.casefold() for u in ids}:
            raise ValueError("login emails must be unique and distinct from user ids")
        for user in self.users:
            if user.role not in self.roles:
                raise ValueError(f"user {user.id} references unknown role {user.role}")
        return self


def load_access_config(path: Path) -> AccessConfig:
    return AccessConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
