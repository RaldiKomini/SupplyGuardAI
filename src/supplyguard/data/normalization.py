"""Deterministic conversion of CSV strings to typed database values."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, Table

TRUE_VALUES = {"1", "true", "yes", "y"}
NULL_VALUES = {"", "unknown", "n/a", "null", "none"}


def normalize_identifier(value: str | None) -> str | None:
    """Trim identifiers without inventing or silently remapping them."""
    clean = (value or "").strip()
    return clean or None


def parse_value(value: str | None, column_type: Any) -> Any:
    clean = (value or "").strip()
    if clean.casefold() in NULL_VALUES:
        return None
    if isinstance(column_type, Integer):
        return int(Decimal(clean))
    if isinstance(column_type, Numeric):
        return Decimal(clean)
    if isinstance(column_type, Boolean):
        return clean.casefold() in TRUE_VALUES
    if isinstance(column_type, Date):
        return date.fromisoformat(clean)
    if isinstance(column_type, DateTime):
        # Synthetic inputs use ISO-8601. Keep offset information when supplied.
        from datetime import datetime

        return datetime.fromisoformat(clean.replace("Z", "+00:00"))
    return clean


def normalize_row(row: dict[str, str], table: Table) -> dict[str, Any]:
    """Keep only declared columns and apply their explicit SQL types."""
    result: dict[str, Any] = {}
    for column in table.columns:
        if column.name in row:
            result[column.name] = parse_value(row[column.name], column.type)
    return result
