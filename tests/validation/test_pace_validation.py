from travel_planner.domain.models import PlaceLoadTag, PlaceStop, RouteEvidence
from travel_planner.domain.pace import PaceLevel, get_pace_profile
from travel_planner.validation.pace import PaceStatus, evaluate_pace


def test_relaxed_route_above_ninety_minutes_creates_blocking_conflict():
    result = evaluate_pace(
        places=[PlaceStop(name="Osaka Castle"), PlaceStop(name="Kaiyukan")],
        route=RouteEvidence(
            total_required_transfer_minutes=142,
            max_single_transfer_minutes=56,
            walking_distance_km=4.2,
        ),
        profile=get_pace_profile(PaceLevel.RELAXED),
    )

    assert result.status is PaceStatus.CONFLICT
    assert "total_required_transfer_minutes" in result.reasons


def test_full_day_high_load_rejects_second_major_place():
    result = evaluate_pace(
        places=[
            PlaceStop(name="Universal Studios Japan", load_tag=PlaceLoadTag.FULL_DAY_HIGH_LOAD),
            PlaceStop(name="Dotonbori"),
        ],
        route=RouteEvidence(
            total_required_transfer_minutes=45,
            max_single_transfer_minutes=20,
            walking_distance_km=2,
        ),
        profile=get_pace_profile(PaceLevel.STANDARD),
    )

    assert result.status is PaceStatus.CONFLICT
    assert "full_day_high_load" in result.reasons
