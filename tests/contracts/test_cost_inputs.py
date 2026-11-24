from decimal import Decimal

import pytest
from pydantic import ValidationError

from supplyguard.contracts import PurchaseCostInput, TransferCostInput


@pytest.mark.parametrize(
    ("model", "rate_field"),
    [
        (PurchaseCostInput, "unit_price_eur"),
        (TransferCostInput, "transfer_cost_eur_per_unit"),
    ],
)
@pytest.mark.parametrize(
    ("quantity", "rate"),
    [(-1, "1"), (1.5, "1"), (1, "-0.01"), (1, "NaN")],
)
def test_cost_inputs_reject_invalid_quantities_and_rates(model, rate_field, quantity, rate):
    with pytest.raises(ValidationError):
        model(quantity=quantity, **{rate_field: Decimal(rate)})
