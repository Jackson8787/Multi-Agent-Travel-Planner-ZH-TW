from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from travel_planner.domain.models import (
    DayPlanState,
    ExchangeRateSnapshot,
    PlaceStop,
    PriceRecord,
    PriceStatus,
    RouteEvidence,
    TripSpec,
)
from travel_planner.domain.pace import PaceLevel, PaceProfile, get_pace_profile


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (PaceLevel.VERY_RELAXED, (2, 75, 30, 4)),
        (PaceLevel.RELAXED, (2, 90, 35, 6)),
        (PaceLevel.STANDARD, (3, 120, 50, 10)),
        (PaceLevel.INTENSIVE, (4, 180, 75, 15)),
    ],
)
def test_pace_profiles_have_approved_limits(level, expected):
    profile = get_pace_profile(level)

    assert (
        profile.max_major_places_per_day,
        profile.max_required_transfer_minutes_per_day,
        profile.max_single_transfer_minutes,
        profile.walking_distance_warning_km,
    ) == expected


def test_relaxed_profile_matches_not_too_tired_choice():
    profile = get_pace_profile(PaceLevel.RELAXED)

    assert profile.max_major_places_per_day == 2
    assert profile.max_required_transfer_minutes_per_day == 90
    assert profile.max_single_transfer_minutes == 35
    assert profile.walking_distance_warning_km == 6


def test_returned_pace_profile_cannot_mutate_the_registered_preset():
    profile = get_pace_profile(PaceLevel.RELAXED)

    try:
        profile.max_major_places_per_day = 99
        assert get_pace_profile(PaceLevel.RELAXED).max_major_places_per_day == 2
    finally:
        profile.max_major_places_per_day = 2

    override = get_pace_profile(PaceLevel.RELAXED).model_copy(
        update={"max_major_places_per_day": 1}
    )
    assert override.max_major_places_per_day == 1
    assert get_pace_profile(PaceLevel.RELAXED).max_major_places_per_day == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_major_places_per_day": 0},
        {"max_required_transfer_minutes_per_day": -1},
        {"max_single_transfer_minutes": -1},
        {"walking_distance_warning_km": -0.1},
    ],
)
def test_pace_profile_rejects_invalid_override_thresholds(overrides):
    fields = {
        "level": PaceLevel.RELAXED,
        "max_major_places_per_day": 2,
        "max_required_transfer_minutes_per_day": 90,
        "max_single_transfer_minutes": 35,
        "walking_distance_warning_km": 6,
    }
    fields.update(overrides)

    with pytest.raises(ValidationError):
        PaceProfile(**fields)


def test_pace_profile_accepts_valid_user_override():
    override = PaceProfile(
        level=PaceLevel.RELAXED,
        max_major_places_per_day=1,
        max_required_transfer_minutes_per_day=60,
        max_single_transfer_minutes=20,
        walking_distance_warning_km=3,
    )

    assert override.max_required_transfer_minutes_per_day == 60


def test_pace_profile_rejects_single_transfer_over_daily_total():
    with pytest.raises(ValidationError):
        PaceProfile(
            level=PaceLevel.RELAXED,
            max_major_places_per_day=1,
            max_required_transfer_minutes_per_day=20,
            max_single_transfer_minutes=21,
            walking_distance_warning_km=3,
        )


def test_verified_price_keeps_source_and_original_currency():
    price = PriceRecord(
        item_id="usj-ticket",
        item_name="Universal Studios Japan Ticket",
        category="admission",
        amount_original=8600,
        currency_original="JPY",
        status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
        source_provider="Universal Studios Japan Official Website",
        source_url="https://www.usj.co.jp/web/en/us",
    )

    assert price.currency_original == "JPY"
    assert price.source_url.host == "www.usj.co.jp"


def make_price(**overrides):
    fields = {
        "item_id": "item",
        "item_name": "Item",
        "category": "admission",
        "currency_original": "JPY",
        "status": PriceStatus.API_VERIFIED_EXACT,
        "source_provider": "provider",
    }
    fields.update(overrides)
    return PriceRecord(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": PriceStatus.API_VERIFIED_EXACT},
        {"status": PriceStatus.API_VERIFIED_EXACT, "amount_original": -1},
        {
            "status": PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
            "amount_original_min": 10,
            "amount_original_max": 20,
        },
        {
            "status": PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
            "amount_original": 10,
            "amount_original_min": 10,
        },
        {"status": PriceStatus.API_VERIFIED_RANGE, "amount_original_min": 10},
        {
            "status": PriceStatus.API_VERIFIED_RANGE,
            "amount_original_min": -1,
            "amount_original_max": 10,
        },
        {
            "status": PriceStatus.API_VERIFIED_RANGE,
            "amount_original_min": 20,
            "amount_original_max": 10,
        },
        {
            "status": PriceStatus.API_VERIFIED_RANGE,
            "amount_original": 10,
            "amount_original_min": 10,
            "amount_original_max": 20,
        },
        {"status": PriceStatus.MISSING_PRICE, "amount_original": 10},
        {
            "status": PriceStatus.MISSING_PRICE,
            "amount_original_min": 10,
            "amount_original_max": 20,
        },
    ],
)
def test_price_records_reject_amount_shapes_incompatible_with_status(overrides):
    with pytest.raises(ValidationError):
        make_price(**overrides)


def test_price_records_accept_valid_exact_range_and_missing_shapes():
    exact = make_price(status=PriceStatus.API_VERIFIED_EXACT, amount_original=Decimal("8600"))
    official = make_price(
        status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
        amount_original=Decimal("8600"),
    )
    price_range = make_price(
        status=PriceStatus.API_VERIFIED_RANGE,
        amount_original_min=Decimal("100"),
        amount_original_max=Decimal("200"),
    )
    missing = make_price(status=PriceStatus.MISSING_PRICE)

    assert exact.amount_original == Decimal("8600")
    assert official.amount_original == Decimal("8600")
    assert price_range.amount_original_min == Decimal("100")
    assert missing.amount_original is None


def test_exchange_rate_requires_positive_rate():
    with pytest.raises(ValidationError):
        ExchangeRateSnapshot(
            provider="provider",
            base_currency="JPY",
            target_currency="TWD",
            rate=0,
            retrieved_at=datetime.now(timezone.utc),
        )


def test_trip_budget_requires_non_negative_amount():
    with pytest.raises(ValidationError):
        TripSpec(
            destination="Osaka",
            days=5,
            budget_amount=-1,
            interests=[],
            pace=get_pace_profile(PaceLevel.RELAXED),
            hotel=PlaceStop(name="Hotel"),
        )


def test_trip_budget_override_history_requires_non_negative_amounts():
    with pytest.raises(ValidationError):
        TripSpec(
            destination="Osaka",
            days=5,
            budget_amount=25000,
            interests=[],
            pace=get_pace_profile(PaceLevel.RELAXED),
            hotel=PlaceStop(name="Hotel"),
            budget_override_history=[Decimal("25000"), Decimal("-1")],
        )


def test_trip_spec_add_budget_override_rejects_negative_amount():
    trip = TripSpec(
        destination="Osaka",
        days=5,
        budget_amount=25000,
        interests=[],
        pace=get_pace_profile(PaceLevel.RELAXED),
        hotel=PlaceStop(name="Hotel"),
    )

    with pytest.raises(ValidationError):
        trip.add_budget_override(Decimal("-1"))


def test_trip_spec_add_budget_override_appends_valid_amount():
    trip = TripSpec(
        destination="Osaka",
        days=5,
        budget_amount=25000,
        interests=[],
        pace=get_pace_profile(PaceLevel.RELAXED),
        hotel=PlaceStop(name="Hotel"),
    )

    trip.add_budget_override(Decimal("28000"))

    assert trip.budget_override_history == [Decimal("28000")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_required_transfer_minutes": -1},
        {"max_single_transfer_minutes": -1},
        {"walking_distance_km": -0.1},
    ],
)
def test_route_evidence_rejects_negative_quantities(overrides):
    fields = {
        "total_required_transfer_minutes": 90,
        "max_single_transfer_minutes": 35,
        "walking_distance_km": 6,
    }
    fields.update(overrides)

    with pytest.raises(ValidationError):
        RouteEvidence(**fields)


def test_route_evidence_rejects_single_transfer_longer_than_total():
    with pytest.raises(ValidationError):
        RouteEvidence(
            total_required_transfer_minutes=30,
            max_single_transfer_minutes=31,
            walking_distance_km=1,
        )


@pytest.mark.parametrize("overrides", [{"day": 0}, {"retry_count": -1}])
def test_day_plan_state_rejects_invalid_counters(overrides):
    fields = {"day": 1, "retry_count": 0}
    fields.update(overrides)

    with pytest.raises(ValidationError):
        DayPlanState(**fields)


def test_closed_state_fields_are_typed_enums():
    from travel_planner.domain.models import DayPlanStatus, PlaceLoadTag, RateType

    fx_snapshot = ExchangeRateSnapshot(
        provider="provider",
        base_currency="JPY",
        target_currency="TWD",
        rate=Decimal("0.22"),
        retrieved_at=datetime.now(timezone.utc),
    )
    place = PlaceStop(name="Universal Studios Japan", load_tag=PlaceLoadTag.FULL_DAY_HIGH_LOAD)
    day = DayPlanState(day=1, status=DayPlanStatus.VALIDATING)

    assert fx_snapshot.rate_type is RateType.INDICATIVE_MIDPOINT
    assert place.load_tag is PlaceLoadTag.FULL_DAY_HIGH_LOAD
    assert DayPlanState(day=1).status is DayPlanStatus.DRAFT
    assert day.status is DayPlanStatus.VALIDATING

    with pytest.raises(ValidationError):
        DayPlanState(day=1, status="TYPO")
