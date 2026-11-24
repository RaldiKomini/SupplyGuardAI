"""Stable identity for the exact actions a human is asked to approve."""

import hashlib
import json

from supplyguard.contracts import RecoveryAction


def canonical_payload(actions: tuple[RecoveryAction, ...]) -> str:
    """Serialise actions the same way every time.

    Pydantic's JSON mode turns Decimal and date values into strings, and
    sort_keys fixes the key order, so equal actions always give equal text.
    """
    data = [action.model_dump(mode="json") for action in actions]
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def payload_hash(actions: tuple[RecoveryAction, ...]) -> str:
    digest = hashlib.sha256(canonical_payload(actions).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
