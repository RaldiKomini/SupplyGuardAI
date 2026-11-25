"""Re-check and re-price a modified payload before it becomes a new approval request.

A reviewer can change quantities or drop actions, but the stated costs are never trusted:
every action is checked against the case data and priced again by the cost service.
"""

from collections import defaultdict
from decimal import Decimal

from supplyguard.contracts import (
    PurchaseCostInput,
    RecoveryAction,
    RecoveryActionType,
    TransferCostInput,
)
from supplyguard.planning.options import PlanningInputs
from supplyguard.planning.screening import (
    APPROVED_ERP_STATUS,
    VALID_CERTIFICATION,
    validate_order_quantity,
)


class RevalidationError(ValueError):
    """The modified payload cannot become an approval request."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def reprice_actions(
    actions: tuple[RecoveryAction, ...], inputs: PlanningInputs
) -> tuple[tuple[RecoveryAction, ...], tuple[str, ...]]:
    """Return the actions with recalculated costs, and the problems found (empty if valid)."""
    repriced: list[RecoveryAction] = []
    problems: list[str] = []
    for action in actions:
        if action.action_type == RecoveryActionType.PURCHASE_ALTERNATIVE:
            new_action, found = _purchase(action, inputs)
        elif action.action_type == RecoveryActionType.TRANSFER_INVENTORY:
            new_action, found = _transfer(action, inputs)
        elif action.action_type == RecoveryActionType.EXPEDITE_ORDER:
            new_action, found = _expedite(action, inputs)
        else:
            new_action, found = None, [f"{action.action_id}: {action.action_type} cannot be priced"]
        problems.extend(found)
        if new_action is not None:
            repriced.append(new_action)
    problems.extend(_transfer_stock_problems(actions, inputs))
    problems.extend(_supplier_capacity_problems(actions, inputs))

    total = sum((action.cost_eur for action in repriced), Decimal("0"))
    cap = inputs.incident.spending_cap_eur
    if not problems and total > cap:
        problems.append(f"total {total} EUR exceeds the {cap} EUR spending cap")
    return tuple(repriced), tuple(problems)


def _purchase(action: RecoveryAction, inputs: PlanningInputs):
    supplier_id = str(action.payload.get("supplier_id"))
    capability = next(
        (c for c in inputs.impact.supplier_capabilities if c.supplier_id == supplier_id), None
    )
    if capability is None or supplier_id == inputs.incident.supplier_id:
        return None, [f"{action.action_id}: {supplier_id} is not an alternative supplier"]
    problems = []
    if capability.certification_status != VALID_CERTIFICATION:
        problems.append(f"{action.action_id}: {supplier_id} certification is not valid")
    if capability.erp_status != APPROVED_ERP_STATUS:
        problems.append(f"{action.action_id}: {supplier_id} is not approved in the ERP")
    quantity = int(action.payload.get("qty", 0))
    problems.extend(
        f"{action.action_id}: {reason}" for reason in validate_order_quantity(capability, quantity)
    )
    if problems:
        return None, problems
    trace = inputs.costs.estimate_purchase(
        PurchaseCostInput(
            quantity=quantity, unit_price_eur=capability.unit_price_eur, sources=capability.sources
        )
    )
    payload = {**action.payload, "unit_price_eur": str(capability.unit_price_eur)}
    return _with_cost(action, payload, trace), []


def _transfer(action: RecoveryAction, inputs: PlanningInputs):
    quantity = int(action.payload.get("qty", 0))
    if quantity <= 0:
        return None, [f"{action.action_id}: quantity must be positive"]
    hub = _location(inputs, str(action.payload.get("from_location")))
    if hub is None:
        return None, [f"{action.action_id}: unknown location {action.payload.get('from_location')}"]
    trace = inputs.costs.estimate_transfer(
        TransferCostInput(
            quantity=quantity,
            transfer_cost_eur_per_unit=inputs.transfer_cost_eur_per_unit,
            sources=hub.sources,
        )
    )
    return _with_cost(action, action.payload, trace), []


def _transfer_stock_problems(actions: tuple[RecoveryAction, ...], inputs: PlanningInputs):
    """Transfers from one location may not move more than its usable stock."""
    moved: dict[str, int] = defaultdict(int)
    for action in actions:
        if action.action_type == RecoveryActionType.TRANSFER_INVENTORY:
            moved[str(action.payload.get("from_location"))] += int(action.payload.get("qty", 0))
    problems = []
    for location_id, quantity in moved.items():
        position = _location(inputs, location_id)
        if position is not None and quantity > position.usable_units:
            problems.append(
                f"transfers move {quantity} units from {location_id}, "
                f"which has {position.usable_units} usable"
            )
    return problems


def _supplier_capacity_problems(actions: tuple[RecoveryAction, ...], inputs: PlanningInputs):
    """Several purchases from one supplier may not add up to more than its capacity."""
    ordered: dict[tuple[str, str], int] = defaultdict(int)
    for action in actions:
        if action.action_type == RecoveryActionType.PURCHASE_ALTERNATIVE:
            supplier_id = str(action.payload.get("supplier_id"))
            component_id = str(action.payload.get("component_id", inputs.incident.component_id))
            ordered[(supplier_id, component_id)] += int(action.payload.get("qty", 0))
    problems = []
    for (supplier_id, component_id), quantity in ordered.items():
        capability = next(
            (
                c
                for c in inputs.impact.supplier_capabilities
                if c.supplier_id == supplier_id and c.component_id == component_id
            ),
            None,
        )
        if capability is not None and quantity > capability.monthly_capacity_units:
            problems.append(
                f"purchases from {supplier_id} for {component_id} total {quantity} units, "
                f"above its monthly capacity of {capability.monthly_capacity_units}"
            )
    return problems


def _expedite(action: RecoveryAction, inputs: PlanningInputs):
    po_id = action.payload.get("po_id")
    order = next((po for po in inputs.impact.open_purchase_orders if po.po_id == po_id), None)
    if order is None:
        return None, [f"{action.action_id}: no open purchase order {po_id}"]
    if not order.expedite_allowed:
        return None, [f"{action.action_id}: purchase order {po_id} cannot be expedited"]
    trace = inputs.costs.estimate_expedite(order)
    payload = {**action.payload, "qty": order.quantity}
    return _with_cost(action, payload, trace), []


def _location(inputs: PlanningInputs, location_id: str):
    return next((c for c in inputs.impact.coverage if c.location_id == location_id), None)


def _with_cost(action: RecoveryAction, payload, trace) -> RecoveryAction:
    return action.model_copy(
        update={"payload": payload, "cost_eur": Decimal(trace.result), "sources": trace.sources}
    )
