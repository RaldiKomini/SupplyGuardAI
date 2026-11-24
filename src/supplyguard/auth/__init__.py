"""Authentication and role-to-scope mapping for the application layer."""

from supplyguard.auth.config import AccessConfig, RoleConfig, UserConfig, load_access_config
from supplyguard.auth.registry import AccessRegistry, get_registry
from supplyguard.auth.secret import require_auth_secret

__all__ = [
    "AccessConfig",
    "AccessRegistry",
    "RoleConfig",
    "UserConfig",
    "get_registry",
    "load_access_config",
    "require_auth_secret",
]
