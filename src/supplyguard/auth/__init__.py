"""Authentication and role-to-scope mapping for the application layer."""

from supplyguard.auth.config import AccessConfig, RoleConfig, UserConfig, load_access_config
from supplyguard.auth.registry import AccessRegistry, get_registry

__all__ = [
    "AccessConfig",
    "AccessRegistry",
    "RoleConfig",
    "UserConfig",
    "get_registry",
    "load_access_config",
]
