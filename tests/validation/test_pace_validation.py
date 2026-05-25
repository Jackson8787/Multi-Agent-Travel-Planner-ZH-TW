from travel_planner.domain.models import PlaceLoadTag, PlaceStop, RouteEvidence, WalkingPreference
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


def test_short_walk_only_promotes_excessive_walking_to_conflict():
    # RELAXED threshold is 6km; SHORT_WALK_ONLY halves it to 3km
    result = evaluate_pace(
        places=[PlaceStop(name="Osaka Castle"), PlaceStop(name="Dotonbori")],
        route=RouteEvidence(
            total_required_transfer_minutes=45,
            max_single_transfer_minutes=20,
            walking_distance_km=3.5,  # above 3km cut but below normal 6km threshold
        ),
        profile=get_pace_profile(PaceLevel.RELAXED),
        walking_preference=WalkingPreference.SHORT_WALK_ONLY,
    )

    assert result.status is PaceStatus.CONFLICT
    assert "walking_distance_short_walk_only" in result.reasons


def test_prefer_walking_raises_threshold_so_moderate_distance_passes():
    # RELAXED threshold is 6km; PREFER_WALKING raises it to 9km
    result = evaluate_pace(
        places=[PlaceStop(name="Osaka Castle"), PlaceStop(name="Dotonbori")],
        route=RouteEvidence(
            total_required_transfer_minutes=45,
            max_single_transfer_minutes=20,
            walking_distance_km=8.0,  # above 6km but below 9km raised threshold
        ),
        profile=get_pace_profile(PaceLevel.RELAXED),
        walking_preference=WalkingPreference.PREFER_WALKING,
    )

    assert result.status is PaceStatus.PASSED


def test_normal_walking_preference_keeps_existing_warning_threshold():
    # RELAXED threshold is 6km; 7km should still be WARNING
    result = evaluate_pace(
        places=[PlaceStop(name="Osaka Castle"), PlaceStop(name="Dotonbori")],
        route=RouteEvidence(
            total_required_transfer_minutes=45,
            max_single_transfer_minutes=20,
            walking_distance_km=7.0,
        ),
        profile=get_pace_profile(PaceLevel.RELAXED),
        walking_preference=WalkingPreference.NORMAL,
    )

    assert result.status is PaceStatus.WARNING
