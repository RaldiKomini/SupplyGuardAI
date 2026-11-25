"""Pins the ApprovalRequestSource contract that CaseService depends on."""

import inspect

from supplyguard.app.case_service import CaseService
from supplyguard.contracts.protocols import ApprovalRequestSource


def test_protocol_exposes_only_the_read_side_lookups() -> None:
    members = {name for name in vars(ApprovalRequestSource) if not name.startswith("_")}
    assert members == {"get_request", "list_open_for", "has_decided"}
    for name in ("get_request", "list_open_for", "has_decided"):
        assert inspect.iscoroutinefunction(getattr(ApprovalRequestSource, name))


def test_get_request_takes_a_single_request_id() -> None:
    parameters = list(inspect.signature(ApprovalRequestSource.get_request).parameters)
    assert parameters == ["self", "approval_request_id"]


def test_inbox_and_decided_lookups_take_user_scoped_arguments() -> None:
    open_parameters = list(inspect.signature(ApprovalRequestSource.list_open_for).parameters)
    decided_parameters = list(inspect.signature(ApprovalRequestSource.has_decided).parameters)
    assert open_parameters == ["self", "role", "user_id"]
    assert decided_parameters == ["self", "approval_request_id", "user_id"]


def test_case_service_accepts_the_contract_type() -> None:
    hints = inspect.get_annotations(CaseService.__init__)
    assert hints["approvals"] == ApprovalRequestSource | None
