from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from travel_planner.domain.models import ExchangeRateSnapshot, PriceRecord, PriceStatus


class BudgetStatus(StrEnum):
    PASSED = "PASSED"
    OVER_BUDGET = "OVER_BUDGET"
    POSSIBLE_OVER_BUDGET = "POSSIBLE_OVER_BUDGET"
    MISSING_PRICE = "MISSING_PRICE"


class BudgetOutcome(BaseModel):
    status: BudgetStatus
    confirmed_total: Decimal = Field(ge=0)
    minimum_total: Decimal = Field(ge=0)
    maximum_total: Decimal = Field(ge=0)
    missing_item_ids: list[str] = Field(default_factory=list)


def _converted(amount: Decimal, fx: ExchangeRateSnapshot) -> Decimal:
    return (amount * fx.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def evaluate_budget(
    prices: list[PriceRecord], fx: ExchangeRateSnapshot, limit: Decimal
) -> BudgetOutcome:
    confirmed = Decimal("0")
    minimum = Decimal("0")
    maximum = Decimal("0")
    missing: list[str] = []

    for price in prices:
        if price.status is PriceStatus.MISSING_PRICE:
            missing.append(price.item_id)
            continue
        if price.status is PriceStatus.API_VERIFIED_RANGE:
            minimum += _converted(price.amount_original_min or Decimal("0"), fx)
            maximum += _converted(price.amount_original_max or Decimal("0"), fx)
            continue

        exact = _converted(price.amount_original or Decimal("0"), fx)
        confirmed += exact
        minimum += exact
        maximum += exact

    if missing:
        status = BudgetStatus.MISSING_PRICE
    elif confirmed > limit:
        status = BudgetStatus.OVER_BUDGET
    elif maximum > limit:
        status = BudgetStatus.POSSIBLE_OVER_BUDGET
    else:
        status = BudgetStatus.PASSED

    return BudgetOutcome(
        status=status,
        confirmed_total=confirmed,
        minimum_total=minimum,
        maximum_total=maximum,
        missing_item_ids=missing,
    )
