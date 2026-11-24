"""Idempotent CSV loader: exact raw staging followed by typed core upserts."""

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from supplyguard.contracts import DataQualityWarning
from supplyguard.data.normalization import normalize_row
from supplyguard.data.schema import TABLES_BY_DATASET, data_quality_issues, raw_records
from supplyguard.data.validation import validate_inventory_row, validate_required_fields


@dataclass(frozen=True)
class LoadSummary:
    staged_rows: int
    core_rows: int
    warnings: tuple[DataQualityWarning, ...]


class StructuredDataLoader:
    """Loads a directory without altering its files or hiding malformed records."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def prepare_schemas(self) -> None:
        await self.connection.execute(text("CREATE SCHEMA IF NOT EXISTS staging"))
        await self.connection.execute(text("CREATE SCHEMA IF NOT EXISTS core"))

    async def load_directory(self, directory: Path) -> LoadSummary:
        staged = 0
        core = 0
        warnings: list[DataQualityWarning] = []
        for path in sorted(directory.glob("*.csv")):
            dataset = path.stem
            rows = self._read_csv(path)
            await self._replace_staging(dataset, path, rows)
            staged += len(rows)

            table = TABLES_BY_DATASET.get(dataset)
            if table is None:
                if dataset == "data_quality_manifest":
                    core += await self._load_quality_manifest(rows)
                continue

            required_columns = tuple(
                column.name
                for column in table.columns
                if not column.nullable
                and column.default is None
                and column.server_default is None
                and column.autoincrement is not True
            )
            warnings.extend(validate_required_fields(rows, dataset, required_columns))
            if dataset == "inventory":
                for row in rows:
                    warnings.extend(validate_inventory_row(row))
            core += await self._upsert_rows(table, rows)
        await self._persist_validation_warnings(warnings)
        return LoadSummary(staged_rows=staged, core_rows=core, warnings=tuple(warnings))

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    async def _replace_staging(self, dataset: str, path: Path, rows: list[dict[str, str]]) -> None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        await self.connection.execute(
            delete(raw_records).where(raw_records.c.dataset_name == dataset)
        )
        if not rows:
            return
        key_names = list(TABLES_BY_DATASET.get(dataset, raw_records).primary_key.columns.keys())
        records = []
        for number, row in enumerate(rows, start=2):
            record_key = "|".join(row.get(name, "") for name in key_names) or None
            records.append(
                {
                    "dataset_name": dataset,
                    "row_number": number,
                    "record_key": record_key,
                    "payload": row,
                    "source_path": path.name,
                    "source_hash": digest,
                }
            )
        await self.connection.execute(insert(raw_records), records)

    async def _upsert_rows(self, table, rows: list[dict[str, str]]) -> int:
        normalized = [normalize_row(row, table) for row in rows]
        # Blocking invalid rows remain preserved in staging but never become authoritative facts.
        required_columns = tuple(
            column.name
            for column in table.columns
            if not column.nullable
            and column.default is None
            and column.server_default is None
            and column.autoincrement is not True
        )
        normalized = [
            row for row in normalized if all(row.get(name) is not None for name in required_columns)
        ]
        if not normalized:
            return 0
        statement = pg_insert(table).values(normalized)
        primary_keys = [column.name for column in table.primary_key.columns]
        updates = {
            column.name: statement.excluded[column.name]
            for column in table.columns
            if column.name not in primary_keys
        }
        statement = statement.on_conflict_do_update(index_elements=primary_keys, set_=updates)
        await self.connection.execute(statement)
        return len(normalized)

    async def _load_quality_manifest(self, rows: list[dict[str, str]]) -> int:
        values = []
        for row in rows:
            issue_type = row.get("issue_type", "unknown")
            blocking_types = {"missing_id", "conflicting_dates"}
            severity = "blocking" if issue_type in blocking_types else "warning"
            values.append(
                {
                    "issue_type": issue_type,
                    "file": row.get("file", "unknown"),
                    "record_ref": row.get("record_ref") or None,
                    "note": row.get("note", "No detail supplied"),
                    "severity": severity,
                }
            )
        if not values:
            return 0
        statement = pg_insert(data_quality_issues).values(values)
        statement = statement.on_conflict_do_nothing(constraint="uq_data_issue")
        await self.connection.execute(statement)
        return len(values)

    async def _persist_validation_warnings(self, warnings: list[DataQualityWarning]) -> None:
        values = [
            {
                "issue_type": warning.issue_type,
                "file": warning.sources[0].source_id if warning.sources else "validation",
                "record_ref": warning.record_id,
                "note": warning.message,
                "severity": warning.severity.value,
            }
            for warning in warnings
        ]
        if values:
            statement = pg_insert(data_quality_issues).values(values)
            await self.connection.execute(
                statement.on_conflict_do_nothing(constraint="uq_data_issue")
            )
