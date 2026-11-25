"""Authenticates users and builds their `AuthorizationContext`."""

import os
from collections.abc import Mapping
from functools import cache
from hmac import compare_digest
from pathlib import Path

from supplyguard.auth.config import AccessConfig, UserConfig, load_access_config
from supplyguard.contracts import AuthorizationContext

DEFAULT_CONFIG_PATH = "config/access.toml"


class AccessRegistry:
    def __init__(self, config: AccessConfig, environ: Mapping[str, str] | None = None) -> None:
        self._config = config
        self._users = {user.id: user for user in config.users}
        self._emails = {user.email.casefold(): user for user in config.users if user.email}
        self._environ = os.environ if environ is None else environ

    def authenticate(self, user_id: str, password: str) -> AuthorizationContext | None:
        login = user_id.strip()
        user = self._users.get(login) or self._emails.get(login.casefold())
        expected = self._environ.get(user.password_env, "") if user else ""
        # Compare even for unknown users so timing does not reveal which ids exist.
        matches = compare_digest(password.encode(), expected.encode())
        if user is None or not expected or not matches:
            return None
        return self._context_for(user)

    def authorization_for(self, user_id: str) -> AuthorizationContext | None:
        user = self._users.get(user_id)
        return None if user is None else self._context_for(user)

    def display_name(self, user_id: str) -> str | None:
        user = self._users.get(user_id)
        return None if user is None else user.display_name

    def _context_for(self, user: UserConfig) -> AuthorizationContext:
        return AuthorizationContext(
            user_id=user.id,
            role=user.role,
            factory_ids=user.factory_ids,
            allowed_scopes=self._config.roles[user.role].scopes,
        )


@cache
def get_registry() -> AccessRegistry:
    path = Path(os.environ.get("ACCESS_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    return AccessRegistry(load_access_config(path))
