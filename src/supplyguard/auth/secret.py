"""Startup check for the session-signing secret."""

import os
from collections.abc import Mapping

MIN_SECRET_LENGTH = 32


def require_auth_secret(environ: Mapping[str, str] | None = None) -> str:
    secret = (os.environ if environ is None else environ).get("CHAINLIT_AUTH_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"CHAINLIT_AUTH_SECRET must be set to at least {MIN_SECRET_LENGTH} characters "
            "(generate one with `uv run chainlit create-secret`)."
        )
    return secret
