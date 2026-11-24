import pytest

from supplyguard.agents.contract_agent.agent import analyze_contracts
from supplyguard.agents.contract_agent.tools import (
    ALLOWED_TOOL_NAMES,
    ContractTools,
    check_tool_allowlist,
)

# Scopes as defined in support-data/config/users.csv
PROCUREMENT_MANAGER = {"public", "internal", "procurement"}


def test_contract_versions_newest_first(service):
    versions = ContractTools(service, PROCUREMENT_MANAGER).list_contract_versions("S001")
    assert [v["source_id"] for v in versions] == ["DOC-CON-S001-v3", "DOC-CON-S001-v2"]
    assert versions[1]["status"] == "superseded"


def test_structured_findings_have_citations(service):
    analysis = analyze_contracts(
        service, ["S001", "S002", "S003", "S004", "S005"], PROCUREMENT_MANAGER
    )
    assert analysis.findings
    for f in analysis.findings:
        assert f.source_id and f.version >= 1 and f.effective_date not in ("", "None")
    assert "DOC-CON-S001-v2" in analysis.superseded_sources
    assert any("DOC-NOT-HELIOS-PRICE" in s for s in analysis.security_findings)
    borealis = next(
        f
        for f in analysis.findings
        if f.supplier_id == "S003" and f.topic == "certification_status"
    )
    assert borealis.value == "expired"


def test_agent_refuses_procurement_write_tools():
    class FakeTool:
        def __init__(self, name):
            self.name = name

    check_tool_allowlist([FakeTool(n) for n in ALLOWED_TOOL_NAMES])  # ok
    with pytest.raises(PermissionError):
        check_tool_allowlist([FakeTool("search_documents"), FakeTool("create_purchase_order")])


def test_tools_cannot_widen_scope(service):
    viewer_tools = ContractTools(service, {"public"})
    assert viewer_tools.list_contract_versions("S001") == []  # contracts are 'procurement'
    assert viewer_tools.get_supplier_terms("S003")["planning_values"] == {}


def test_langchain_tools_are_read_only(service):
    pytest.importorskip("langchain_core")
    tools = ContractTools(service, PROCUREMENT_MANAGER).as_langchain_tools()
    assert {t.name for t in tools} == ALLOWED_TOOL_NAMES
