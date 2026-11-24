"""Shared fixtures for the evidence-layer tests (George).

The tests read the supplied support-data folder and use the in-memory vector store
with the local hashing embedder: no database, API key or network is needed.

support-data is not committed to the repository (see COLLABORATION.md). Point
SUPPLYGUARD_SUPPORT_DATA at your local copy, or put it at the repository root.
Without it these tests are skipped, so CI stays green.
"""

import os
from pathlib import Path

import pytest

from supplyguard.retrieval.service import EvidenceService

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_DATA = Path(os.getenv("SUPPLYGUARD_SUPPORT_DATA", REPO_ROOT / "support-data"))


@pytest.fixture(scope="session")
def support_data() -> Path:
    if not (SUPPORT_DATA / "documents").is_dir():
        pytest.skip(f"support-data not found at {SUPPORT_DATA} (set SUPPLYGUARD_SUPPORT_DATA)")
    return SUPPORT_DATA


@pytest.fixture(scope="session")
def service(support_data) -> EvidenceService:
    return EvidenceService.from_support_data(support_data, backend="memory")
