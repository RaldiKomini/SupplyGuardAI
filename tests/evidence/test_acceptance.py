"""George's acceptance tests (one test per line of the role description)."""

from supplyguard.guardrails.untrusted import format_evidence_for_prompt

# Scopes as defined in support-data/config/users.csv
PROCUREMENT_MANAGER = {"public", "internal", "procurement"}
FINANCE_CONTROLLER = {"public", "internal", "procurement", "finance-confidential"}
VIEWER = {"public"}


def test_atlas_contract_v3_wins_over_v2(service):
    facts = service.supplier_facts("S001", PROCUREMENT_MANAGER)
    lead = facts.planning_values["lead_time_days"]
    assert lead.value == 14
    assert lead.citation.source_id == "DOC-CON-S001-v3"
    # v2 (10 days) is reported as a superseded alternative, not silently dropped
    conflict = next(c for c in facts.conflicts if c.fact_name == "lead_time_days")
    assert conflict.alternatives[0]["value"] == 10
    assert conflict.alternatives[0]["use_as"] == "ignore: superseded"


def test_v3_ranks_above_v2_in_search(service):
    result = service.search("Atlas Components contract lead time", PROCUREMENT_MANAGER, k=20)
    ids = [e.citation.source_id for e in result.evidence]
    assert ids[0] == "DOC-CON-S001-v3"
    assert ids.index("DOC-CON-S001-v3") < ids.index("DOC-CON-S001-v2")


def test_old_atlas_lead_time_request_returns_v2_marked_superseded(service):
    """EV-0099 'Use the old 10-day Atlas lead time': v2 can be found, but is clearly marked."""
    result = service.search("old 10-day Atlas lead time", PROCUREMENT_MANAGER, supplier_id="S001")
    v2 = next(e for e in result.evidence if e.citation.source_id == "DOC-CON-S001-v2")
    assert v2.citation.status == "superseded"
    assert any(c.fact_name == "lead_time_days" and c.planning_value == 14 for c in result.conflicts)


def test_formal_notice_is_baseline_and_email_is_sensitivity(service):
    facts = service.supplier_facts("S001", PROCUREMENT_MANAGER)
    assert facts.planning_values["delay_days"].value == 12
    assert facts.planning_values["delay_days"].citation.source_id == "DOC-NOT-ATLAS-MC17"
    conflict = next(c for c in facts.conflicts if c.fact_name == "delay_days")
    assert conflict.alternatives == [conflict.alternatives[0]]
    assert conflict.alternatives[0]["value"] == 16
    assert conflict.alternatives[0]["use_as"] == "sensitivity"


def test_borealis_returned_with_expired_certificate(service):
    facts = service.supplier_facts("S003", PROCUREMENT_MANAGER)
    assert facts.certification_valid is False
    assert facts.planning_values["certification_status"].citation.source_id == "DOC-QUA-S003-v1"
    assert facts.planning_values["certificate_expiry"].value == "2026-09-30"


def test_source_conflicts_are_preserved(service):
    cordillera = service.supplier_facts("S004", PROCUREMENT_MANAGER)
    fee = next(c for c in cordillera.conflicts if c.fact_name == "expedite_fee_pct")
    assert fee.planning_value == 6 and fee.alternatives[0]["value"] == 10

    helios = service.supplier_facts("S002", PROCUREMENT_MANAGER)
    cap = next(c for c in helios.conflicts if c.fact_name == "monthly_capacity_units")
    assert cap.planning_value == 4000
    assert cap.alternatives[0]["value"] == 1500
    assert cap.alternatives[0]["source"].source_id == "DOC-EM-HELIOS-01"


def test_unauthorized_finance_documents_never_reach_the_model(service):
    for scopes in (PROCUREMENT_MANAGER, VIEWER):
        result = service.search("margin by customer tier negotiated penalty", scopes, k=20)
        assert all(e.citation.access_scope in scopes for e in result.evidence)
        prompt = format_evidence_for_prompt(result.evidence)
        assert "DOC-FIN-01" not in prompt and "gross margin" not in prompt.lower()
    # ... but the finance controller does get it. We check the top 3, not only #1:
    # DOC-PRO-07 (customer prioritisation) also talks about customer tiers and may rank first.
    result = service.search("margin by customer tier", FINANCE_CONTROLLER)
    top3 = [e.citation.source_id for e in result.evidence[:3]]
    assert "DOC-FIN-01-v1" in top3


def test_viewer_only_sees_public_documents(service):
    result = service.search("Atlas contract lead time", VIEWER, k=20)
    assert {e.citation.access_scope for e in result.evidence} <= {"public"}


def test_unknown_auth_context_gives_no_access(service):
    assert service.search("Atlas", None).evidence == []
    assert service.search("Atlas", {"allowed_scopes": ["superuser"]}).evidence == []


def test_prompt_injection_detected_ignored_and_reported(service):
    result = service.search("Helios price update MC-17", PROCUREMENT_MANAGER)
    helios = next(e for e in result.evidence if e.citation.source_id == "DOC-NOT-HELIOS-PRICE")
    # reported
    categories = {f.category for f in result.security_findings}
    assert {"approval_bypass", "false_certification", "instruction_override"} <= categories
    # ignored: the instruction text is gone, the legitimate price is still there
    text = helios.chunk.text.lower()
    assert "without human approval" not in text and "ignore all previous" not in text
    assert "7.20" in text
    # and it cannot change Helios' certification status (only qualification records can)
    facts = service.supplier_facts("S002", PROCUREMENT_MANAGER)
    assert facts.planning_values["certification_status"].citation.doc_type == "qualification"


def test_approved_memories_are_stored_separately(service):
    docs = service.search(
        "MC-17 Atlas resolved Purchase from an approved alternative", PROCUREMENT_MANAGER, k=20
    )
    assert all(e.citation.doc_type != "approved_incident_memory" for e in docs.evidence)
    memories = service.search_incident_memories(
        "MC-17 Atlas certification lapse", PROCUREMENT_MANAGER
    )
    assert memories.evidence and all(
        e.citation.doc_type == "approved_incident_memory" for e in memories.evidence
    )


def test_new_approved_memory_is_indexed_and_searchable(service):
    service.index_approved_memory(
        {
            "memory_id": "MEM-GOLD-001",
            "case_id": "GOLD-001",
            "component_id": "MC-17",
            "supplier_id": "S001",
            "strategy": "Transfer + expedite + Cordillera purchase",
            "approved_summary": "Atlas 12-day delay on MC-17 resolved with hub transfer, "
            "PO-2026-9002 expedite "
            "and 3,000 units from Cordillera at EUR 30,378.",
            "access_scope": "internal",
        }
    )
    result = service.search_incident_memories("Cordillera hub transfer MC-17", PROCUREMENT_MANAGER)
    assert result.evidence[0].citation.source_id == "MEM-GOLD-001"


def test_every_claim_has_source_version_and_effective_date(service):
    result = service.search("MC-17 delay lead time capacity expedite", PROCUREMENT_MANAGER, k=20)
    for e in result.evidence:
        c = e.citation
        assert c.source_id and c.version >= 1 and c.effective_date is not None
        assert e.chunk.text.startswith(
            f"[{c.source_id} | {c.doc_type} | v{c.version} | effective {c.effective_date}"
        )
    for conflict in result.conflicts:
        assert conflict.planning_source.effective_date is not None
