"""The ONLY tools the contract agent gets: read-only document retrieval.

The user's access scopes are fixed when the tools are built (closure), so the model
cannot ask for a wider scope: there is simply no scope parameter it could set.
"""

from __future__ import annotations

from supplyguard.guardrails.access import scopes_from

ALLOWED_TOOL_NAMES = {"search_documents", "get_supplier_terms", "list_contract_versions"}

# Names of the procurement write tools (Athanasia's MCP). Used as a safety check.
FORBIDDEN_TOOL_NAMES = {
    "create_purchase_order",
    "expedite_purchase_order",
    "request_stock_transfer",
    "update_production_schedule",
}


class ContractTools:
    def __init__(self, service, auth_context):
        self.service = service
        self.scopes = scopes_from(auth_context)

    def search_documents(self, query: str, supplier_id: str = "", doc_type: str = "") -> dict:
        """Search contracts, qualification records, notices, e-mails and procedures.
        Optional filters: supplier_id (e.g. 'S001'), doc_type (e.g. 'contract')."""
        result = self.service.search(
            query,
            self.scopes,
            k=6,
            supplier_id=supplier_id or None,
            doc_types=[doc_type] if doc_type else None,
        )
        return {
            "results": [
                {
                    "source_id": e.citation.source_id,
                    "doc_type": e.citation.doc_type,
                    "version": e.citation.version,
                    "effective_date": str(e.citation.effective_date),
                    "status": e.citation.status,
                    "text": e.chunk.text,
                }
                for e in result.evidence
            ],
            "security_findings": [f"{f.source_id}: {f.category}" for f in result.security_findings],
            "notes": result.notes,
        }

    def get_supplier_terms(self, supplier_id: str) -> dict:
        """Lead time, capacity, expedite fee, delay and certification for one supplier,
        already resolved by source precedence, with every conflict listed."""
        facts = self.service.supplier_facts(supplier_id, self.scopes)
        return {
            "supplier_id": supplier_id,
            "planning_values": {
                name: {
                    "value": f.value,
                    "source_id": f.citation.source_id,
                    "version": f.citation.version,
                    "effective_date": str(f.citation.effective_date),
                }
                for name, f in facts.planning_values.items()
            },
            "certification_valid": facts.certification_valid,
            "conflicts": [c.explanation for c in facts.conflicts],
            "security_findings": [f"{f.source_id}: {f.category}" for f in facts.security_findings],
        }

    def list_contract_versions(self, supplier_id: str) -> list[dict]:
        """Every contract version on file for a supplier, newest first, with status."""
        versions = {}
        for chunk in self.service.chunks:
            c = chunk.citation
            if (
                c.doc_type == "contract"
                and c.supplier_id == supplier_id
                and c.access_scope in self.scopes
            ):
                versions[c.source_id] = {
                    "source_id": c.source_id,
                    "version": c.version,
                    "effective_date": str(c.effective_date),
                    "status": c.status,
                }
        return sorted(
            versions.values(), key=lambda v: (v["effective_date"], v["version"]), reverse=True
        )

    def as_langchain_tools(self) -> list:
        from langchain_core.tools import StructuredTool

        tools = [
            StructuredTool.from_function(getattr(self, name), name=name)
            for name in sorted(ALLOWED_TOOL_NAMES)
        ]
        check_tool_allowlist(tools)
        return tools


def check_tool_allowlist(tools: list) -> None:
    """Refuse to build an agent with any tool outside the read-only allowlist."""
    names = {getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools}
    if names & FORBIDDEN_TOOL_NAMES or not names <= ALLOWED_TOOL_NAMES:
        raise PermissionError(
            f"contract agent may only use {sorted(ALLOWED_TOOL_NAMES)}, got {sorted(names)}"
        )
