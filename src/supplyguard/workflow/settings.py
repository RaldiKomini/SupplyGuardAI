"""Values the workflow reads from the supplied configuration."""

import json
import os
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "support-data"


def data_dir() -> Path:
    """support-data/ at the repository root; SUPPORT_DATA_DIR overrides it (e.g. in Docker).
    An empty value, as in a copied .env.example, means the default."""
    return Path(os.getenv("SUPPORT_DATA_DIR") or DEFAULT_DATA_DIR)


DATA_DIR = data_dir()


@cache
def policies() -> dict:
    return json.loads((DATA_DIR / "config" / "policies.json").read_text(encoding="utf-8"))


def policy_spending_cap() -> Decimal:
    """The incident spending cap: the default, and the most intake will ever apply."""
    return Decimal(str(policies()["incident_spending_cap_eur"]))


def scenario_date() -> date:
    return date.fromisoformat(policies()["scenario_date"])
