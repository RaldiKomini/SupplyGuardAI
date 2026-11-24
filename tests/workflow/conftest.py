"""Test stand-ins for the workflow: a scripted model and a simple master-data lookup.

FakeMasterData only exists for tests; the real MasterDataService is Raldi's component.
"""

import csv

import pytest
from langchain_core.runnables import RunnableLambda

from supplyguard.contracts import ComponentCandidate, SupplierCandidate
from supplyguard.workflow.settings import DATA_DIR


def _rows(name: str) -> list[dict]:
    with open(DATA_DIR / "structured" / name, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


class FakeMasterData:
    """Name lookups over the supplied CSVs: suppliers whose name starts with the query words,
    components whose ID matches or whose description contains every query word."""

    def __init__(self) -> None:
        self.suppliers = [
            SupplierCandidate(supplier_id=r["supplier_id"], name=r["name"])
            for r in _rows("suppliers.csv")
        ]
        self.components = [
            ComponentCandidate(component_id=r["component_id"], description=r["description"])
            for r in _rows("components.csv")
        ]
        self.links = _rows("supplier_components.csv")

    async def find_suppliers(self, query: str) -> tuple[SupplierCandidate, ...]:
        words = query.lower().split()
        return tuple(
            s
            for s in self.suppliers
            if s.supplier_id.lower() == query.lower().strip()
            or s.name.lower().split()[: len(words)] == words
        )

    async def find_components(self, query: str) -> tuple[ComponentCandidate, ...]:
        words = [w for w in query.lower().replace("-", " ").split() if w not in {"the", "a"}]
        exact = [c for c in self.components if c.component_id.lower() == query.lower().strip()]
        described = [c for c in self.components if all(w in c.description.lower() for w in words)]
        return tuple(exact or described)

    async def suppliers_of(self, component_id: str) -> tuple[SupplierCandidate, ...]:
        ids = {r["supplier_id"] for r in self.links if r["component_id"] == component_id}
        return tuple(s for s in self.suppliers if s.supplier_id in ids)


class ScriptedModel:
    """Returns the given extractions in order, one per call."""

    def __init__(self, *extractions) -> None:
        self.extractions = list(extractions)

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda _prompt: self.extractions.pop(0))


@pytest.fixture
def master_data() -> FakeMasterData:
    return FakeMasterData()


@pytest.fixture
def scripted_model():
    return ScriptedModel
