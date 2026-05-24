from enum import StrEnum

from pydantic import BaseModel, Field

from travel_planner.domain.models import PlaceLoadTag, PlaceStop, RouteEvidence
from travel_planner.domain.pace import PaceProfile


class PaceStatus(StrEnum):
    PASSED = "PASSED"
    CONFLICT = "CONFLICT"
    WARNING = "WARNING"


class PaceOutcome(BaseModel):
    status: PaceStatus
    reasons: list[str] = Field(default_factory=list)


def evaluate_pace(
    places: list[PlaceStop], route: RouteEvidence, profile: PaceProfile
) -> PaceOutcome:
    reasons: list[str] = []

    if len(places) > profile.max_major_places_per_day:
        reasons.append("major_place_count")
    if route.total_required_transfer_minutes > profile.max_required_transfer_minutes_per_day:
        reasons.append("total_required_transfer_minutes")
    if route.max_single_transfer_minutes > profile.max_single_transfer_minutes:
        reasons.append("max_single_transfer_minutes")
    if any(place.load_tag is PlaceLoadTag.FULL_DAY_HIGH_LOAD for place in places) and len(places) > 1:
        reasons.append("full_day_high_load")

    if reasons:
        return PaceOutcome(status=PaceStatus.CONFLICT, reasons=reasons)
    if route.walking_distance_km > profile.walking_distance_warning_km:
        return PaceOutcome(status=PaceStatus.WARNING, reasons=["walking_distance_warning"])
    return PaceOutcome(status=PaceStatus.PASSED)
