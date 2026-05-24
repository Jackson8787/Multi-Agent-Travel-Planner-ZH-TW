from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl

from travel_planner.domain.pace import PaceProfile


class PriceStatus(StrEnum):
    API_VERIFIED_EXACT = "API_VERIFIED_EXACT"
    USER_CONFIRMED_OFFICIAL_SOURCE = "USER_CONFIRMED_OFFICIAL_SOURCE"
    API_VERIFIED_RANGE = "API_VERIFIED_RANGE"
    MISSING_PRICE = "MISSING_PRICE"


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


class ExchangeRateSnapshot(BaseModel):
    provider: str
    base_currency: str
    target_currency: str
    rate: Decimal
    retrieved_at: datetime
    rate_type: str = "INDICATIVE_MIDPOINT"


class PlaceStop(BaseModel):
    name: str
    place_id: str | None = None
    locked: bool = False
    load_tag: str = "FLEXIBLE_VISIT"


class TripSpec(BaseModel):
    destination: str
    days: int = Field(ge=1, le=5)
    budget_amount: Decimal
    budget_currency: str = "TWD"
    interests: list[str]
    pace: PaceProfile
    hotel: PlaceStop
    must_visit: list[PlaceStop] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
    fx_snapshot: ExchangeRateSnapshot | None = None
    budget_override_history: list[Decimal] = Field(default_factory=list)


class RouteEvidence(BaseModel):
    total_required_transfer_minutes: int
    max_single_transfer_minutes: int
    walking_distance_km: float
    encoded_polyline: str | None = None
    source_provider: str = "Google Routes API"


class DayPlanState(BaseModel):
    day: int
    status: str = "DRAFT"
    places: list[PlaceStop] = Field(default_factory=list)
    meals: list[PlaceStop] = Field(default_factory=list)
    route: RouteEvidence | None = None
    prices: list[PriceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retry_count: int = 0
    quality_score: int | None = None
