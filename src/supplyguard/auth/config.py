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
        for user in self.users:
            if user.role not in self.roles:
                raise ValueError(f"user {user.id} references unknown role {user.role}")
        return self


def load_access_config(path: Path) -> AccessConfig:
    return AccessConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
