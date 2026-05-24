from datetime import UTC, datetime
from decimal import Decimal

from travel_planner.domain.models import ExchangeRateSnapshot, PriceRecord, PriceStatus
from travel_planner.validation.budget import BudgetStatus, evaluate_budget


FX = ExchangeRateSnapshot(
    provider="ExchangeRate-API",
    base_currency="JPY",
    target_currency="TWD",
    rate=Decimal("0.20"),
    retrieved_at=datetime.now(UTC),
)


def test_range_crossing_limit_is_possible_over_budget():
    prices = [
        PriceRecord(
            item_id="hotel",
            item_name="Hotel",
            category="lodging",
            amount_original=Decimal("100000"),
            currency_original="JPY",
            status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
            source_provider="Hotel official",
        ),
        PriceRecord(
            item_id="dinner",
            item_name="Dinner",
            category="meal",
            amount_original_min=Decimal("20000"),
            amount_original_max=Decimal("30000"),
            currency_original="JPY",
            status=PriceStatus.API_VERIFIED_RANGE,
            source_provider="Google Places API (New)",
        ),
    ]

    outcome = evaluate_budget(prices, FX, Decimal("25000"))

    assert outcome.status is BudgetStatus.POSSIBLE_OVER_BUDGET
    assert outcome.confirmed_total == Decimal("20000.00")
    assert outcome.maximum_total == Decimal("26000.00")


def test_missing_price_blocks_verified_pass():
    outcome = evaluate_budget(
        [
            PriceRecord(
                item_id="fare",
                item_name="Transit",
                category="transport",
                currency_original="JPY",
                status=PriceStatus.MISSING_PRICE,
                source_provider="Google Routes API",
            )
        ],
        FX,
        Decimal("25000"),
    )

    assert outcome.status is BudgetStatus.MISSING_PRICE
