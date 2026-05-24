from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class PaceLevel(StrEnum):
    VERY_RELAXED = "VERY_RELAXED"
    RELAXED = "RELAXED"
    STANDARD = "STANDARD"
    INTENSIVE = "INTENSIVE"


class PaceProfile(BaseModel):
    level: PaceLevel
    max_major_places_per_day: int = Field(ge=1)
    max_required_transfer_minutes_per_day: int = Field(ge=0)
    max_single_transfer_minutes: int = Field(ge=0)
    walking_distance_warning_km: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_single_transfer_within_daily_total(self) -> "PaceProfile":
        if self.max_single_transfer_minutes > self.max_required_transfer_minutes_per_day:
            raise ValueError("single transfer minutes cannot exceed the daily transfer total")
        return self


PACE_PROFILES = {
    PaceLevel.VERY_RELAXED: PaceProfile(
        level=PaceLevel.VERY_RELAXED,
        max_major_places_per_day=2,
        max_required_transfer_minutes_per_day=75,
        max_single_transfer_minutes=30,
        walking_distance_warning_km=4,
    ),
    PaceLevel.RELAXED: PaceProfile(
        level=PaceLevel.RELAXED,
        max_major_places_per_day=2,
        max_required_transfer_minutes_per_day=90,
        max_single_transfer_minutes=35,
        walking_distance_warning_km=6,
    ),
    PaceLevel.STANDARD: PaceProfile(
        level=PaceLevel.STANDARD,
        max_major_places_per_day=3,
        max_required_transfer_minutes_per_day=120,
        max_single_transfer_minutes=50,
        walking_distance_warning_km=10,
    ),
    PaceLevel.INTENSIVE: PaceProfile(
        level=PaceLevel.INTENSIVE,
        max_major_places_per_day=4,
        max_required_transfer_minutes_per_day=180,
        max_single_transfer_minutes=75,
        walking_distance_warning_km=15,
    ),
}


def get_pace_profile(level: PaceLevel) -> PaceProfile:
    return PACE_PROFILES[level].model_copy(deep=True)
