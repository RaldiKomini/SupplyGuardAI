"""Approval tiers loaded from config/policies.json."""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class ApprovalTier:
    up_to_eur: Decimal
    approver_roles: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalPolicy:
    tiers: tuple[ApprovalTier, ...]


def load_approval_policy(path: Path) -> ApprovalPolicy:
    """Read the tiers; an approver such as "a + b" means two people are needed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    tiers = tuple(
        ApprovalTier(
            up_to_eur=Decimal(str(tier["up_to"])),
            approver_roles=tuple(role.strip() for role in tier["approver"].split("+")),
        )
        for tier in data["approval_tiers_eur"]
    )
    return ApprovalPolicy(tiers=tuple(sorted(tiers, key=lambda tier: tier.up_to_eur)))


def required_approver_roles(total_cost_eur: Decimal, policy: ApprovalPolicy) -> tuple[str, ...]:
    """Roles that must approve this amount. Above the highest tier nobody may approve."""
    if total_cost_eur < 0:
        raise ValueError("total cost cannot be negative")
    for tier in policy.tiers:
        if total_cost_eur <= tier.up_to_eur:
            return tier.approver_roles
    raise ValueError(f"total cost {total_cost_eur} EUR exceeds the highest approval tier")
