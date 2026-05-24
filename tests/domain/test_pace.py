from travel_planner.domain.models import PriceRecord, PriceStatus
from travel_planner.domain.pace import PaceLevel, get_pace_profile


def test_relaxed_profile_matches_not_too_tired_choice():
    profile = get_pace_profile(PaceLevel.RELAXED)

    assert profile.max_major_places_per_day == 2
    assert profile.max_required_transfer_minutes_per_day == 90
    assert profile.max_single_transfer_minutes == 35
    assert profile.walking_distance_warning_km == 6


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
