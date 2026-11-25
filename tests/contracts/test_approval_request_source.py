"""Pins the ApprovalRequestSource contract that CaseService depends on."""

import inspect

from supplyguard.app.case_service import CaseService
from supplyguard.contracts.protocols import ApprovalRequestSource


def test_protocol_exposes_only_async_get_request() -> None:
    members = {name for name in vars(ApprovalRequestSource) if not name.startswith("_")}
    assert members == {"get_request"}
    assert inspect.iscoroutinefunction(ApprovalRequestSource.get_request)


def test_get_request_takes_a_single_request_id() -> None:
    parameters = list(inspect.signature(ApprovalRequestSource.get_request).parameters)
    assert parameters == ["self", "approval_request_id"]


def test_case_service_accepts_the_contract_type() -> None:
    hints = inspect.get_annotations(CaseService.__init__)
    assert hints["approvals"] == ApprovalRequestSource | None
