from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, model_validator

from travel_planner.domain.pace import PaceProfile


class PriceStatus(StrEnum):
    API_VERIFIED_EXACT = "API_VERIFIED_EXACT"
    USER_CONFIRMED_OFFICIAL_SOURCE = "USER_CONFIRMED_OFFICIAL_SOURCE"
    API_VERIFIED_RANGE = "API_VERIFIED_RANGE"
    MISSING_PRICE = "MISSING_PRICE"


class RateType(StrEnum):
    INDICATIVE_MIDPOINT = "INDICATIVE_MIDPOINT"


class PlaceLoadTag(StrEnum):
    FLEXIBLE_VISIT = "FLEXIBLE_VISIT"
    FULL_DAY_HIGH_LOAD = "FULL_DAY_HIGH_LOAD"
    LONG_VISIT = "LONG_VISIT"
    DAY_TRIP = "DAY_TRIP"


class DayPlanStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class PriceRecord(BaseModel):
    item_id: str
    item_name: str
    category: str
    amount_original: Decimal | None = None
    amount_original_min: Decimal | None = None
    amount_original_max: Decimal | None = None
    currency_original: str
    status: PriceStatus
    source_provider: str
    source_url: HttpUrl | None = None
    retrieved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_amount_shape(self) -> "PriceRecord":
        exact_statuses = {
            PriceStatus.API_VERIFIED_EXACT,
            PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
        }
        has_range = self.amount_original_min is not None or self.amount_original_max is not None

        if self.status in exact_statuses:
            if (
                self.amount_original is None
                or self.amount_original < 0
                or has_range
            ):
                raise ValueError("exact prices require one non-negative exact amount")
        elif self.status == PriceStatus.API_VERIFIED_RANGE:
            if (
                self.amount_original is not None
                or self.amount_original_min is None
                or self.amount_original_max is None
                or self.amount_original_min < 0
                or self.amount_original_max < 0
                or self.amount_original_min > self.amount_original_max
            ):
                raise ValueError("range prices require ordered non-negative bounds only")
        elif self.amount_original is not None or has_range:
            raise ValueError("missing prices cannot contain amounts")

        return self


class ExchangeRateSnapshot(BaseModel):
    provider: str
    base_currency: str
    target_currency: str
    rate: Decimal = Field(gt=0)
    retrieved_at: datetime
    rate_type: RateType = RateType.INDICATIVE_MIDPOINT


class PlaceStop(BaseModel):
    name: str
    place_id: str | None = None
    locked: bool = False
    load_tag: PlaceLoadTag = PlaceLoadTag.FLEXIBLE_VISIT


class TripSpec(BaseModel):
    destination: str
    days: int = Field(ge=1, le=5)
    budget_amount: Decimal = Field(ge=0)
    budget_currency: str = "TWD"
    interests: list[str]
    pace: PaceProfile
    hotel: PlaceStop
    must_visit: list[PlaceStop] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
    fx_snapshot: ExchangeRateSnapshot | None = None
    budget_override_history: list[Annotated[Decimal, Field(ge=0)]] = Field(default_factory=list)


class RouteEvidence(BaseModel):
    total_required_transfer_minutes: int = Field(ge=0)
    max_single_transfer_minutes: int = Field(ge=0)
    walking_distance_km: float = Field(ge=0)
    encoded_polyline: str | None = None
    source_provider: str = "Google Routes API"

    @model_validator(mode="after")
    def validate_single_transfer_within_total(self) -> "RouteEvidence":
        if self.max_single_transfer_minutes > self.total_required_transfer_minutes:
            raise ValueError("single transfer minutes cannot exceed the route total")
        return self


class DayPlanState(BaseModel):
    day: int = Field(ge=1)
    status: DayPlanStatus = DayPlanStatus.DRAFT
    places: list[PlaceStop] = Field(default_factory=list)
    meals: list[PlaceStop] = Field(default_factory=list)
    route: RouteEvidence | None = None
    prices: list[PriceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    quality_score: int | None = None
