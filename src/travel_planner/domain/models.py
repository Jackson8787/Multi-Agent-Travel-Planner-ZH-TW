from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, ValidationError, model_validator

from travel_planner.domain.pace import PaceProfile


class SlotType(StrEnum):
    ATTRACTION = "ATTRACTION"
    LUNCH_PLACEHOLDER = "LUNCH_PLACEHOLDER"
    DINNER_PLACEHOLDER = "DINNER_PLACEHOLDER"
    FREE_TIME = "FREE_TIME"
    # Filled meal slots — Food Agent replaced the placeholder with a real restaurant.
    # Distinct from the PLACEHOLDER variants so the UI can render confirmed meals
    # differently (no "待定" suffix, definitive icon).
    LUNCH = "LUNCH"
    DINNER = "DINNER"


class TimeSlot(BaseModel):
    """One time-boxed block in a day's schedule."""

    start_time: str
    """24-hour HH:MM string, e.g. '09:00'."""
    end_time: str
    """24-hour HH:MM string, e.g. '12:00'."""
    slot_type: SlotType = SlotType.ATTRACTION
    location_name: str = ""
    """Attraction name (ATTRACTION) or area hint (PLACEHOLDER); empty for FREE_TIME."""
    notes: str = ""
    """Optional remarks: entrance tip, seasonal info, nearby landmark, etc."""


class PriceStatus(StrEnum):
    API_VERIFIED_EXACT = "API_VERIFIED_EXACT"
    USER_CONFIRMED_OFFICIAL_SOURCE = "USER_CONFIRMED_OFFICIAL_SOURCE"
    API_VERIFIED_RANGE = "API_VERIFIED_RANGE"
    MISSING_PRICE = "MISSING_PRICE"


class RateType(StrEnum):
    INDICATIVE_MIDPOINT = "INDICATIVE_MIDPOINT"


class RouteMode(StrEnum):
    TRANSIT = "TRANSIT"
    DRIVE = "DRIVE"
    AUTO = "AUTO"


class WalkingPreference(StrEnum):
    NORMAL = "NORMAL"
    PREFER_WALKING = "PREFER_WALKING"
    SHORT_WALK_ONLY = "SHORT_WALK_ONLY"


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


class ConflictType(StrEnum):
    PACE_EXCEEDED = "PACE_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    PRICE_MISSING = "PRICE_MISSING"
    GROUNDING_FAILED = "GROUNDING_FAILED"
    # Phase 1 — macro feasibility
    PHYSICAL_IMPOSSIBILITY = "PHYSICAL_IMPOSSIBILITY"
    # Phase 2 — duplicate place detected during day planning
    DUPLICATE_PLACE = "DUPLICATE_PLACE"


class UserChoice(StrEnum):
    KEEP_PACE_REPLAN = "KEEP_PACE_REPLAN"
    ACCEPT_PACE_WARNING = "ACCEPT_PACE_WARNING"
    INCREASE_DAY_PACE = "INCREASE_DAY_PACE"
    INCREASE_BUDGET_KEEP_PLAN = "INCREASE_BUDGET_KEEP_PLAN"
    KEEP_LOCKED_REDUCE_COST = "KEEP_LOCKED_REDUCE_COST"
    REPLACE_ITEMS_KEEP_BUDGET = "REPLACE_ITEMS_KEEP_BUDGET"
    ACCEPT_COST_WARNING = "ACCEPT_COST_WARNING"
    # Phase 1 — macro decision
    PROCEED_WITH_WARNINGS = "PROCEED_WITH_WARNINGS"
    # Phase 2 — duplicate-place decision
    ALLOW_REVISIT = "ALLOW_REVISIT"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"


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


class GlobalMemory(BaseModel):
    """Mutable trip-wide memory that survives across day-planning iterations."""

    visited_places: list[str] = Field(default_factory=list)
    """Names of every place (attraction + meal) that has been confirmed in an approved day."""
    allowed_duplicates: list[str] = Field(default_factory=list)
    """Places the user explicitly approved to revisit despite appearing in ``visited_places``."""
    remaining_budget: Decimal = Field(ge=0, default=Decimal("0"))
    """Budget remaining in the trip's budget_currency after deducting confirmed costs."""


class MacroDaySlot(BaseModel):
    """One day's assignment in the macro-plan skeleton."""

    day: int = Field(ge=1)
    city: str
    is_travel_day: bool = False
    suggested_theme: str = ""


class MacroCitySegment(BaseModel):
    """LLM-generated city-level overview shown to the user before micro-planning."""

    city: str
    day_range: str
    """Human-readable range, e.g. '第 1-3 天'."""
    theme: str
    """Short evocative phrase describing the city's travel character."""
    hotel_candidates: list[str] = Field(default_factory=list)
    """Up to 3 hotel names proposed by the LLM for the user to choose from."""
    hotel_descriptions: list[str] = Field(default_factory=list)
    """One-line reasoning per hotel candidate — same length and order as hotel_candidates."""
    key_places: list[str] = Field(default_factory=list)
    """3-5 iconic attractions recommended for this city."""
    food_picks: list[str] = Field(default_factory=list)
    """2-3 iconic local foods or restaurant types."""
    notes: str = ""
    """Practical tips: transport, timing, local customs."""


class MacroPlan(BaseModel):
    """Phase 1 output: a skeletal day-by-day city assignment before micro-planning starts."""

    day_slots: list[MacroDaySlot]
    feasibility_warnings: list[str] = Field(default_factory=list)
    has_critical_issues: bool = False
    city_segments: list[MacroCitySegment] = Field(default_factory=list)
    """LLM-generated per-city overviews; populated after calling propose_macro_plan."""


class PlaceStop(BaseModel):
    name: str
    place_id: str | None = None
    locked: bool = False
    load_tag: PlaceLoadTag = PlaceLoadTag.FLEXIBLE_VISIT


class DestinationLeg(BaseModel):
    """One city segment in a multi-city itinerary."""

    city: str
    num_days: int = Field(ge=1, le=10)
    hotel: PlaceStop
    must_visit: list[PlaceStop] = Field(default_factory=list)



class TripSpec(BaseModel):
    destination: str
    days: int = Field(ge=1, le=10)
    budget_amount: Decimal = Field(ge=0)
    budget_currency: str = "TWD"
    interests: list[str]
    pace: PaceProfile
    route_mode: RouteMode = RouteMode.AUTO
    walking_preference: WalkingPreference = WalkingPreference.NORMAL
    hotel: PlaceStop
    must_visit: list[PlaceStop] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
    fx_snapshot: ExchangeRateSnapshot | None = None
    budget_override_history: list[Annotated[Decimal, Field(ge=0)]] = Field(default_factory=list)
    # Multi-city support: when non-empty, the orchestrator uses per-leg
    # city/hotel/must_visit instead of the top-level fields.
    legs: list[DestinationLeg] = Field(default_factory=list)
    # Accumulated user constraints from re-plan feedback ("不要餃子主題" etc.)
    user_constraints: list[str] = Field(default_factory=list)
    # Traveller type resolved during Phase 0 onboarding; used by Food Agent
    # Values: "獨旅" | "情侶" | "家庭" | "未指定" | "" (unknown)
    traveler_type: str = ""

    @property
    def total_days(self) -> int:
        """Total trip duration across all legs (or ``days`` when single-city)."""
        if self.legs:
            return sum(leg.num_days for leg in self.legs)
        return self.days

    def add_budget_override(self, new_limit: Decimal) -> None:
        validated_limit = Decimal(new_limit)
        if validated_limit < 0:
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                [
                    {
                        "type": "greater_than_equal",
                        "loc": ("budget_override_history",),
                        "msg": "Input should be greater than or equal to 0",
                        "input": validated_limit,
                        "ctx": {"ge": 0},
                    }
                ],
            )
        self.budget_override_history.append(validated_limit)


class RouteLeg(BaseModel):
    """One transit hop between two consecutive stops (hotel / attraction / meal)."""

    duration_minutes: int = 0
    travel_mode: str = "TRANSIT"
    """Dominant travel mode: 'TRANSIT' | 'DRIVE' | 'WALK'."""
    walk_distance_km: float = 0.0
    """Actual walking distance within this hop (WALK-only steps)."""


class RouteEvidence(BaseModel):
    total_required_transfer_minutes: int = Field(ge=0)
    max_single_transfer_minutes: int = Field(ge=0)
    walking_distance_km: float = Field(ge=0)
    """Total walking distance across all hops (WALK-mode steps only)."""
    total_distance_km: float = Field(ge=0, default=0.0)
    """Total distance across all transport modes (transit + drive + walking)."""
    encoded_polyline: str | None = None
    encoded_polyline_segments: list[str] = Field(default_factory=list)
    source_provider: str = "Google Routes API"
    legs: list[RouteLeg] = Field(default_factory=list)
    """Per-hop breakdown; length == len(place_ids) - 1 when populated by the Routes client."""

    @model_validator(mode="after")
    def validate_single_transfer_within_total(self) -> "RouteEvidence":
        if self.max_single_transfer_minutes > self.total_required_transfer_minutes:
            raise ValueError("single transfer minutes cannot exceed the route total")
        return self


class DayPlanState(BaseModel):
    day: int = Field(ge=1)
    destination: str = ""
    leg_hotel: PlaceStop | None = None
    status: DayPlanStatus = DayPlanStatus.DRAFT
    places: list[PlaceStop] = Field(default_factory=list)
    meals: list[PlaceStop] = Field(default_factory=list)
    timeslots: list[TimeSlot] = Field(default_factory=list)
    """Resolved day schedule: ATTRACTION + filled meal slots (set by orchestrator)."""
    route: RouteEvidence | None = None
    prices: list[PriceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    quality_score: int | None = None


class ConflictEvent(BaseModel):
    conflict_type: ConflictType
    day: int = Field(ge=1)
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, object] = Field(default_factory=dict)
    status: str = "AWAITING_USER_DECISION"


class UserDecision(BaseModel):
    choice: UserChoice
    new_budget_limit: Decimal | None = None
    new_pace: PaceProfile | None = None
