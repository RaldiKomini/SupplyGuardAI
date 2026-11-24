"""Structured-data integrity checks with explicit severity."""

from collections.abc import Iterable
from datetime import date

from supplyguard.contracts import DataQualityWarning, Severity, SourceReference


def validate_inventory_row(row: dict[str, str]) -> tuple[DataQualityWarning, ...]:
    warnings: list[DataQualityWarning] = []
    source = SourceReference(
        source_id="inventory.csv", source_type="csv", record_id=row.get("component_id")
    )
    try:
        on_hand = int(row["on_hand"])
        reserved = int(row["reserved"])
        quarantined = int(row["quarantined"])
        usable = int(row["usable"])
    except (KeyError, ValueError):
        return (
            DataQualityWarning(
                issue_type="invalid_inventory_number",
                message="Inventory quantities must be whole numbers.",
                severity=Severity.BLOCKING,
                record_id=row.get("component_id"),
                sources=(source,),
            ),
        )
    if min(on_hand, reserved, quarantined, usable) < 0:
        warnings.append(
            DataQualityWarning(
                issue_type="negative_inventory",
                message="Inventory quantities cannot be negative.",
                severity=Severity.BLOCKING,
                record_id=row.get("component_id"),
                sources=(source,),
            )
        )
    if usable != on_hand - reserved - quarantined:
        warnings.append(
            DataQualityWarning(
                issue_type="inventory_balance_mismatch",
                message="Usable stock differs from on-hand minus reserved and quarantined.",
                severity=Severity.BLOCKING,
                record_id=row.get("component_id"),
                sources=(source,),
            )
        )
    return tuple(warnings)


def validate_required_fields(
    rows: Iterable[dict[str, str]], dataset: str, required_columns: tuple[str, ...]
) -> tuple[DataQualityWarning, ...]:
    warnings: list[DataQualityWarning] = []
    for row_number, row in enumerate(rows, start=2):
        missing = [
            column
            for column in required_columns
            if not (row.get(column) or "").strip()
            or (row.get(column) or "").strip().casefold() in {"unknown", "n/a", "null", "none"}
        ]
        if missing:
            warnings.append(
                DataQualityWarning(
                    issue_type="missing_required_field",
                    message=f"Missing required field(s): {', '.join(missing)}.",
                    severity=Severity.BLOCKING,
                    record_id=f"row:{row_number}",
                    sources=(
                        SourceReference(
                            source_id=f"{dataset}.csv",
                            source_type="csv",
                            record_id=f"row:{row_number}",
                        ),
                    ),
                )
            )
    return tuple(warnings)


def stale_inventory_warning(
    component_id: str, last_counted: date | None, scenario_date: date, *, stale_after_days: int = 30
) -> DataQualityWarning | None:
    if last_counted is None or (scenario_date - last_counted).days > stale_after_days:
        return DataQualityWarning(
            issue_type="stale_inventory",
            message=f"Inventory count is absent or older than {stale_after_days} days.",
            severity=Severity.WARNING,
            record_id=component_id,
            sources=(
                SourceReference(
                    source_id="inventory.csv", source_type="csv", record_id=component_id
                ),
            ),
        )
    return None
