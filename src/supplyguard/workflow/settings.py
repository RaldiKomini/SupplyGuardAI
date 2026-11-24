"""Values the workflow reads from the supplied configuration."""

import json
import os
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

# support-data/ at the repository root; SUPPORT_DATA_DIR overrides it (e.g. in Docker).
DATA_DIR = Path(os.getenv("SUPPORT_DATA_DIR", Path(__file__).resolve().parents[3] / "support-data"))


@cache
def policies() -> dict:
    return json.loads((DATA_DIR / "config" / "policies.json").read_text(encoding="utf-8"))


def default_spending_cap() -> Decimal:
    return Decimal(str(policies()["incident_spending_cap_eur"]))


def scenario_date() -> date:
    return date.fromisoformat(policies()["scenario_date"])
