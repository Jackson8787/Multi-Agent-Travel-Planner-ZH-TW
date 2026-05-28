from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from travel_planner.agents.runner import FoodProposal, ReviewResult
from travel_planner.domain.models import (
    ConflictEvent,
    ConflictType,
    DayPlanState,
    DayPlanStatus,
    DestinationLeg,
    ExchangeRateSnapshot,
    GlobalMemory,
    MacroDaySlot,
    MacroPlan,
    PlaceLoadTag,
    PlaceStop,
    PriceRecord,
    PriceStatus,
    SlotType,
    TimeSlot,
    TripSpec,
    UserChoice,
    UserDecision,
)
from travel_planner.domain.pace import PaceLevel, get_pace_profile
from travel_planner.integrations.google_places import GroundingNotFound
from travel_planner.integrations.google_routes import RouteResult, RouteUnavailable
from travel_planner.observability.tracing import NoOpTracer
from travel_planner.validation.budget import BudgetStatus, evaluate_budget
from travel_planner.validation.pace import PaceStatus, evaluate_pace


# ── TimeSlot pipeline helpers ──────────────────────────────────────────────────

def _names_to_timeslots(names: list[str]) -> list[TimeSlot]:
    """Convert a list of place names to synthetic ATTRACTION TimeSlots.

    Assigns consecutive 2-hour windows starting at 09:00 with 1-hour travel
    buffers between slots.  Used as a backward-compatibility shim when fixture /
    test agents still return plain string lists instead of ``TimeSlot`` objects.
    """
    slots: list[TimeSlot] = []
    start_minutes = 9 * 60  # 09:00
    for name in names:
        end_minutes = start_minutes + 120  # 2-hour visit
        slots.append(
            TimeSlot(
                start_time=f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
                end_time=f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
                slot_type=SlotType.ATTRACTION,
                location_name=name,
            )
        )
        start_minutes = end_minutes + 60  # 1-hour travel buffer
    return slots


def _minutes_between(hhmm_start: str, hhmm_end: str) -> int:
    """Return the signed number of minutes from *hhmm_start* to *hhmm_end*."""
    sh, sm = map(int, hhmm_start.split(":"))
    eh, em = map(int, hhmm_end.split(":"))
    return (eh * 60 + em) - (sh * 60 + sm)


def _check_schedule_gaps(
    timeslots: list[TimeSlot], max_single_minutes: int
) -> list[str]:
    """Return conflict reasons for consecutive slot gaps shorter than *max_single_minutes*.

    If the Route API's worst-case transfer already exceeds the buffer between any
    two adjacent slots, that transition is physically impossible.
    """
    reasons: list[str] = []
    for i in range(len(timeslots) - 1):
        gap = _minutes_between(timeslots[i].end_time, timeslots[i + 1].start_time)
        if gap < max_single_minutes:
            reasons.append(
                f"Gap between «{timeslots[i].location_name}» and "
                f"«{timeslots[i + 1].location_name}» is only {gap} min "
                f"but route requires {max_single_minutes} min."
            )
    return reasons


class WorkflowStatus(StrEnum):
    PLANNING = "PLANNING"
    # Phase 1 — macro feasibility check paused for user input
    AWAITING_MACRO_DECISION = "AWAITING_MACRO_DECISION"
    AWAITING_PACE_DECISION = "AWAITING_PACE_DECISION"
    AWAITING_PRICE_DECISION = "AWAITING_PRICE_DECISION"
    AWAITING_BUDGET_DECISION = "AWAITING_BUDGET_DECISION"
    # Phase 2 — duplicate place detected, waiting for user decision
    AWAITING_DUPLICATE_DECISION = "AWAITING_DUPLICATE_DECISION"
    DAY_APPROVED = "DAY_APPROVED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class WorkflowResult(BaseModel):
    status: WorkflowStatus
    day_state: DayPlanState
    conflict: ConflictEvent | None = None


@dataclass
class FakeRouteScenario:
    total_minutes: int
    max_single_minutes: int
    walking_km: float
    polyline: str = "encoded"
    fare: PriceRecord | None = None


class _FixtureAgents:
    def __init__(self, itinerary_sequences: list[list[str]], lunch_sequences: list[str]):
        self._itineraries = iter(itinerary_sequences)
        self._lunches = iter(lunch_sequences)

    def propose_itinerary(
        self,
        destination,
        pace_name,
        visited,
        rejected,
        must_visit,
        remaining_slots,
        route_mode,
        walking_preference,
    ):
        class Proposal:
            def __init__(self, candidates):
                self.candidates = candidates

        return Proposal(candidates=[next(self._itineraries)])

    def propose_food(self, places, remaining_budget=None, traveler_type=""):
        return FoodProposal(lunch_candidates=[next(self._lunches)], dinner_candidates=[])

    def propose_macro_plan(self, destination, total_days, interests, day_slots, hotel_specified, user_constraints=None):
        """Deterministic stub: build one segment per city directly from day_slots."""
        from travel_planner.agents.runner import MacroPlanProposal
        from travel_planner.domain.models import MacroCitySegment

        cities: dict[str, list[int]] = {}
        for slot in day_slots:
            city = slot["city"]
            if city not in cities:
                cities[city] = []
            cities[city].append(slot["day"])
        segments = []
        for city, days in cities.items():
            day_range = (
                f"第 {days[0]}-{days[-1]} 天" if len(days) > 1 else f"第 {days[0]} 天"
            )
            segments.append(
                MacroCitySegment(
                    city=city,
                    day_range=day_range,
                    theme=f"{city} 精華之旅",
                    hotel_candidates=[
                        f"{city} Grand Hotel",
                        f"{city} Central Inn",
                        f"The {city} Hotel",
                    ],
                    hotel_descriptions=[
                        f"位於 {city} 市中心，交通便利，適合觀光。",
                        f"價格實惠，靠近主要景點，性價比高。",
                        f"精品風格，提供舒適住宿體驗。",
                    ],
                    key_places=[f"{city} 代表景點 A", f"{city} 代表景點 B"],
                    food_picks=[f"{city} 特色料理"],
                    notes=f"透過當地大眾運輸即可輕鬆抵達 {city} 各主要景點。",
                )
            )
        return MacroPlanProposal(city_segments=segments)


class _FixturePlaces:
    def __init__(self):
        self._known: dict[str, PlaceStop] = {}

    def ground(self, name: str) -> PlaceStop:
        if name.startswith("MISSING:"):
            raise GroundingNotFound(name)
        place = self._known.get(name)
        if place is None:
            load_tag = PlaceLoadTag.FLEXIBLE_VISIT
            if "Universal Studios Japan" in name:
                load_tag = PlaceLoadTag.FULL_DAY_HIGH_LOAD
            place = PlaceStop(
                name=name,
                place_id=name.lower().replace(" ", "-"),
                load_tag=load_tag,
            )
            self._known[name] = place
        return place


class _FixtureRoutes:
    def __init__(self, results: list[FakeRouteScenario | Exception]):
        self._results = iter(results)

    def compute_daily_route(self, place_ids, *, route_mode=None):
        current = next(self._results)
        if isinstance(current, Exception):
            raise current
        from travel_planner.domain.models import RouteEvidence

        return RouteResult(
            evidence=RouteEvidence(
                total_required_transfer_minutes=current.total_minutes,
                max_single_transfer_minutes=current.max_single_minutes,
                walking_distance_km=current.walking_km,
                encoded_polyline=current.polyline,
            ),
            transit_fare=current.fare,
        )


class _FixturePriceCollector:
    def __init__(self, sequences: list[list[PriceRecord]]):
        self._sequences = iter(sequences)

    def collect(self, day_state, route_result):
        return next(self._sequences)


class _FixtureReviewer:
    def review(self, day_state):
        return ReviewResult(score=4, summary="ok", warnings=[])


class _FixtureTracer:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, name, payload):
        self.events.append((name, payload))


class DemoFixtures:
    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def trip_spec() -> TripSpec:
        return TripSpec(
            destination="Osaka",
            days=5,
            budget_amount=Decimal("25000"),
            interests=["anime", "food"],
            pace=get_pace_profile(PaceLevel.RELAXED),
            hotel=PlaceStop(name="Hotel", place_id="hotel"),
            fx_snapshot=ExchangeRateSnapshot(
                provider="ExchangeRate-API",
                base_currency="JPY",
                target_currency="TWD",
                rate=Decimal("0.20"),
                retrieved_at=DemoFixtures.now(),
            ),
        )

    @staticmethod
    def first_route_too_tiring() -> dict:
        return {
            "trip_spec": DemoFixtures.trip_spec(),
            "agents": _FixtureAgents(
                itinerary_sequences=[
                    ["Osaka Castle", "Kaiyukan"],
                    ["Dotonbori", "Shinsekai"],
                ],
                lunch_sequences=["Dotonbori Lunch"],
            ),
            "places": _FixturePlaces(),
            "routes": _FixtureRoutes(
                [
                    FakeRouteScenario(total_minutes=142, max_single_minutes=56, walking_km=4.2),
                    FakeRouteScenario(total_minutes=68, max_single_minutes=28, walking_km=3.1),
                    FakeRouteScenario(total_minutes=70, max_single_minutes=28, walking_km=3.6),
                ]
            ),
            "price_collector": _FixturePriceCollector([[]]),
            "reviewer": _FixtureReviewer(),
            "tracer": _FixtureTracer(),
        }

    @staticmethod
    def possible_budget_overrun() -> dict:
        trip_spec = DemoFixtures.trip_spec()
        trip_spec.prices.append(
            PriceRecord(
                item_id="hotel",
                item_name="Hotel",
                category="lodging",
                amount_original=Decimal("100000"),
                currency_original="JPY",
                status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
                source_provider="Hotel official",
            )
        )
        return {
            "trip_spec": trip_spec,
            "agents": _FixtureAgents(
                itinerary_sequences=[["Dotonbori", "Shinsekai"]],
                lunch_sequences=["Expensive Lunch"],
            ),
            "places": _FixturePlaces(),
            "routes": _FixtureRoutes(
                [
                    FakeRouteScenario(total_minutes=68, max_single_minutes=28, walking_km=3.1),
                    FakeRouteScenario(total_minutes=70, max_single_minutes=28, walking_km=3.2),
                ]
            ),
            "price_collector": _FixturePriceCollector(
                [
                    [
                        PriceRecord(
                            item_id="meal",
                            item_name="Meal",
                            category="meal",
                            amount_original_min=Decimal("20000"),
                            amount_original_max=Decimal("30000"),
                            currency_original="JPY",
                            status=PriceStatus.API_VERIFIED_RANGE,
                            source_provider="Google Places API (New)",
                        )
                    ]
                ]
            ),
            "reviewer": _FixtureReviewer(),
            "tracer": _FixtureTracer(),
        }

    @staticmethod
    def restaurant_causes_detour() -> dict:
        return {
            "trip_spec": DemoFixtures.trip_spec(),
            "agents": _FixtureAgents(
                itinerary_sequences=[["Dotonbori", "Shinsekai"]],
                lunch_sequences=["Far Lunch"],
            ),
            "places": _FixturePlaces(),
            "routes": _FixtureRoutes(
                [
                    FakeRouteScenario(total_minutes=68, max_single_minutes=28, walking_km=3.1),
                    FakeRouteScenario(total_minutes=110, max_single_minutes=40, walking_km=4.5),
                ]
            ),
            "price_collector": _FixturePriceCollector([[]]),
            "reviewer": _FixtureReviewer(),
            "tracer": _FixtureTracer(),
        }

    @staticmethod
    def routes_fail_twice() -> dict:
        return {
            "trip_spec": DemoFixtures.trip_spec(),
            "agents": _FixtureAgents(
                itinerary_sequences=[
                    ["Dotonbori", "Shinsekai"],
                    ["Dotonbori", "Shinsekai"],
                ],
                lunch_sequences=["Unused Lunch"],
            ),
            "places": _FixturePlaces(),
            "routes": _FixtureRoutes(
                [
                    RouteUnavailable("first failure"),
                    RouteUnavailable("second failure"),
                ]
            ),
            "price_collector": _FixturePriceCollector([[]]),
            "reviewer": _FixtureReviewer(),
            "tracer": _FixtureTracer(),
        }


class TravelWorkflow:
    def __init__(self, trip_spec, agents, places, routes, price_collector, reviewer, tracer):
        self.trip_spec: TripSpec = trip_spec
        self.agents = agents
        self.places = places
        self.routes = routes
        self.price_collector = price_collector
        self.reviewer = reviewer
        self.tracer = tracer or NoOpTracer()
        self.current_day: DayPlanState | None = None
        self.approved_days: list[DayPlanState] = []
        # ── Task 1: scoped constraint lists ───────────────────────────────
        # Permanent: grounding failures + user-rejected duplicate places.
        # Cleared only by explicit user action (not between days).
        self._permanent_rejections: list[str] = []
        # Day-scoped: route failures, pace/budget replan instructions, reviewer vetoes.
        # Automatically cleared when start_day moves to a new day number.
        self._day_constraints: list[str] = []
        self._current_planning_day: int = 0
        # ── Task 2: candidate fallback pool ──────────────────────────────
        # Extra candidate sets returned by propose_itinerary.  When a pace
        # conflict fires, the next set is popped and tried automatically
        # before the workflow pauses for the user.
        self._pending_candidates: list[list[TimeSlot]] = []
        self.pending_warnings: list[str] = []
        # Phase 1 & 2 additions
        self.global_memory: GlobalMemory = GlobalMemory(remaining_budget=trip_spec.budget_amount)
        self.macro_plan: MacroPlan | None = None
        # Name of the duplicate place awaiting the user's allow/reject decision
        self._pending_duplicate_name: str | None = None
        # Real-time progress callback — injected by start_day() / resume() from the UI.
        # Recursive internal calls inherit the existing callback (don't pass None to reset it).
        self._progress_cb: Callable[[str], None] | None = None
        # Metrics callback — distributes (agent_name, in_tokens, out_tokens, elapsed_s) tuples to the UI.
        # Propagated to agents via set_metrics_callback() on every start_day / resume call.
        self._metrics_cb: Callable[[str, int, int, float], None] | None = None

    @classmethod
    def from_fixtures(cls, fixtures: dict) -> "TravelWorkflow":
        return cls(**fixtures)

    def _emit(self, message: str) -> None:
        """Fire a real-time progress update to the UI layer (no-op when no callback is set)."""
        if self._progress_cb is not None:
            self._progress_cb(message)

    def _leg_for_day(self, day: int) -> DestinationLeg | None:
        """Return the DestinationLeg that owns ``day``, or None for single-city trips."""
        if not self.trip_spec.legs:
            return None
        cumulative = 0
        for leg in self.trip_spec.legs:
            cumulative += leg.num_days
            if day <= cumulative:
                return leg
        return self.trip_spec.legs[-1]

    # ── Phase 1: Macro-Planning ────────────────────────────────────────────

    def validate_macro(self) -> WorkflowResult:
        """Phase 1 entry point.

        Performs rule-based feasibility checks on the trip spec and produces the
        ``MacroPlan`` skeleton.  If critical issues are detected the workflow pauses
        and returns ``AWAITING_MACRO_DECISION`` so the UI can prompt the user.
        Otherwise it transparently calls ``start_day(1)`` to begin micro-planning.
        """
        warnings: list[str] = []
        has_critical = False
        pace_max = self.trip_spec.pace.max_major_places_per_day

        if self.trip_spec.legs:
            legs = self.trip_spec.legs
            # Each city needs at least 1 day (Pydantic already enforces num_days ≥ 1,
            # but we still check the aggregate).
            if self.trip_spec.total_days < len(legs):
                warnings.append(
                    f"城市數量（{len(legs)} 個）超過總旅遊天數（{self.trip_spec.total_days} 天）。"
                    f"建議每個城市至少安排 1 天。"
                )
                has_critical = True
            for i, leg in enumerate(legs):
                capacity = leg.num_days * pace_max
                if len(leg.must_visit) > capacity:
                    warnings.append(
                        f"城市 {i + 1}「{leg.city}」：必去景點 {len(leg.must_visit)} 個"
                        f" > {leg.num_days} 天 × {pace_max} 個/天 = {capacity} 個上限。"
                    )
                    has_critical = True
        else:
            # Single-city
            capacity = self.trip_spec.days * pace_max
            if len(self.trip_spec.must_visit) > capacity:
                warnings.append(
                    f"必去景點 {len(self.trip_spec.must_visit)} 個"
                    f" > {self.trip_spec.days} 天 × {pace_max} 個/天 = {capacity} 個上限。"
                )
                has_critical = True

        # Build day-slot skeleton
        day_slots: list[MacroDaySlot] = []
        if self.trip_spec.legs:
            day_counter = 1
            for leg in self.trip_spec.legs:
                for _ in range(leg.num_days):
                    day_slots.append(MacroDaySlot(day=day_counter, city=leg.city))
                    day_counter += 1
        else:
            for d in range(self.trip_spec.days):
                day_slots.append(
                    MacroDaySlot(day=d + 1, city=self.trip_spec.destination)
                )

        self.macro_plan = MacroPlan(
            day_slots=day_slots,
            feasibility_warnings=warnings,
            has_critical_issues=has_critical,
        )
        self.tracer.event(
            "macro_plan_created",
            {
                "day_slots": [s.model_dump() for s in day_slots],
                "has_critical_issues": has_critical,
                "warnings": warnings,
            },
        )

        # Call LLM to generate per-city overview for user confirmation.
        # This is non-fatal: if the call fails the segments list stays empty
        # and the UI gracefully falls back to the skeleton view.
        try:
            day_slots_data = [
                {"day": s.day, "city": s.city, "is_travel_day": s.is_travel_day}
                for s in day_slots
            ]
            destinations = (
                ", ".join(leg.city for leg in self.trip_spec.legs)
                if self.trip_spec.legs
                else self.trip_spec.destination
            )
            hotel_specified = bool(self.trip_spec.legs or self.trip_spec.hotel.name)
            proposal = self.agents.propose_macro_plan(
                destination=destinations,
                total_days=self.trip_spec.total_days,
                interests=self.trip_spec.interests,
                day_slots=day_slots_data,
                hotel_specified=hotel_specified,
                user_constraints=self.trip_spec.user_constraints or None,
            )
            self.macro_plan.city_segments = proposal.city_segments
            self.tracer.event(
                "macro_plan_segments_generated",
                {"count": len(proposal.city_segments)},
            )
        except Exception:
            pass  # LLM failure is non-fatal; UI falls back to skeleton

        # Always pause for user confirmation — regardless of whether critical
        # issues were found — so the traveller can review the overview first.
        conflict = (
            ConflictEvent(
                conflict_type=ConflictType.PHYSICAL_IMPOSSIBILITY,
                day=1,
                reasons=warnings,
                evidence={"macro_plan": [s.model_dump() for s in day_slots]},
            )
            if has_critical
            else None
        )
        return WorkflowResult(
            status=WorkflowStatus.AWAITING_MACRO_DECISION,
            day_state=DayPlanState(day=1, status=DayPlanStatus.DRAFT),
            conflict=conflict,
        )

    # ── Phase 2: Micro-Planning ────────────────────────────────────────────

    def start_day(
        self,
        day: int,
        progress_callback: Callable[[str], None] | None = None,
        metrics_callback: Callable[[str, int, int, float], None] | None = None,
    ) -> WorkflowResult:
        # Register callbacks only when explicitly provided — recursive internal calls inherit.
        if progress_callback is not None:
            self._progress_cb = progress_callback
        if metrics_callback is not None:
            self._metrics_cb = metrics_callback
        # Distribute metrics callback to every LLM inside the agent suite
        _set_mcb = getattr(self.agents, "set_metrics_callback", None)
        if _set_mcb is not None:
            _set_mcb(self._metrics_cb)
        # ── Task 1 & 2: Day-transition bookkeeping ────────────────────────
        if day != self._current_planning_day:
            # Moving to a new day — clear day-scoped constraints and cached
            # candidates.  Permanent rejections are never cleared here.
            self._day_constraints = []
            self._pending_candidates = []
            self._current_planning_day = day
            existing_retry_count = 0  # Retry budget resets per day
        else:
            existing_retry_count = self.current_day.retry_count if self.current_day else 0

        leg = self._leg_for_day(day)
        destination = leg.city if leg is not None else self.trip_spec.destination
        must_visit_pool = leg.must_visit if leg is not None else self.trip_spec.must_visit
        hotel = leg.hotel if leg is not None else self.trip_spec.hotel
        locked_places = must_visit_pool[: self.trip_spec.pace.max_major_places_per_day]
        remaining_slots = self.trip_spec.pace.max_major_places_per_day - len(locked_places)
        previously_visited = [
            place.name
            for prev_day in self.approved_days
            for place in prev_day.places + prev_day.meals
        ]
        candidate_timeslots: list[TimeSlot] = []
        if remaining_slots > 0:
            # ── Task 2: Use next stored candidate set when pace auto-retry ──
            if self._pending_candidates:
                self._emit("♻️ 使用備用行程方案（自動重試）…")
                candidate_timeslots = self._pending_candidates.pop(0)
            else:
                self._emit("🤖 行程 Agent 正在規劃景點時間表…")
                proposal = self.agents.propose_itinerary(
                    destination,
                    self.trip_spec.pace.level.value,
                    visited=[place.name for place in locked_places] + previously_visited,
                    rejected=self._permanent_rejections + self._day_constraints,
                    must_visit=[place.name for place in locked_places],
                    remaining_slots=remaining_slots,
                    route_mode=self.trip_spec.route_mode.value,
                    walking_preference=self.trip_spec.walking_preference.value,
                )
                raw = proposal.candidates[0]
                # Backward-compat: fixture / test agents may still return plain strings
                candidate_timeslots = (
                    _names_to_timeslots(raw)
                    if raw and isinstance(raw[0], str)
                    else list(raw)
                )
                # Cache extra candidate sets for automatic pace fallback (convert if needed)
                if len(proposal.candidates) > 1:
                    self._pending_candidates = [
                        (_names_to_timeslots(c) if c and isinstance(c[0], str) else list(c))
                        for c in proposal.candidates[1:]
                    ]
        else:
            # No new slots needed — synthesise a timed schedule for the locked places
            candidate_timeslots = _names_to_timeslots([p.name for p in locked_places])
        try:
            proposed_places: list[PlaceStop] = []
            for slot in candidate_timeslots:
                if slot.slot_type is not SlotType.ATTRACTION:
                    continue  # PLACEHOLDER slots are filled later by Food Agent
                grounded = self.places.ground(slot.location_name)
                if any(
                    existing.place_id == grounded.place_id or existing.name == grounded.name
                    for existing in [*locked_places, *proposed_places]
                ):
                    continue
                proposed_places.append(grounded)
                if len(proposed_places) >= remaining_slots:
                    break
            self._emit(f"📍 已驗證 {len(proposed_places)} 個地點（Google Places）…")
            places = [*locked_places, *proposed_places]
        except GroundingNotFound as error:
            # Read retry count only from the current day's state (not a previous day)
            previous_retries = (
                self.current_day.retry_count
                if self.current_day and self.current_day.day == day
                else 0
            )
            retries = previous_retries + 1
            self._permanent_rejections.append(f"REJECT_PLACE:{error.query}")
            self._pending_candidates = []  # Discard cache; get fresh candidates on retry
            failed = DayPlanState(day=day, retry_count=retries, warnings=["GROUNDING_FAILED"])
            self.current_day = failed
            self.tracer.event("grounding_failed", {"day": day, "query": error.query, "retry": retries})
            if retries >= 2:
                return WorkflowResult(status=WorkflowStatus.NEEDS_MANUAL_REVIEW, day_state=failed)
            return self.start_day(day)

        # ── Phase 2: duplicate detection ──────────────────────────────────
        # Locked must-visit places are intentionally repeated; only check agent-proposed ones.
        duplicate: PlaceStop | None = None
        for place in proposed_places:
            if (
                place.name in self.global_memory.visited_places
                and place.name not in self.global_memory.allowed_duplicates
            ):
                duplicate = place
                break

        self.current_day = DayPlanState(
            day=day,
            destination=destination,
            leg_hotel=hotel,
            places=places,
            warnings=list(self.pending_warnings),
            retry_count=existing_retry_count,
            status=DayPlanStatus.VALIDATING,
        )
        # Store the raw time-boxed schedule (ATTRACTION + PLACEHOLDER slots).
        # Food Agent will fill PLACEHOLDERs in _route_and_pace_then_complete.
        self.current_day.timeslots = list(candidate_timeslots)
        self.pending_warnings = []

        if duplicate is not None:
            self._pending_duplicate_name = duplicate.name
            self.tracer.event(
                "duplicate_place_detected",
                {"day": day, "place": duplicate.name},
            )
            conflict = ConflictEvent(
                conflict_type=ConflictType.DUPLICATE_PLACE,
                day=day,
                reasons=[f"「{duplicate.name}」在之前的行程中已安排過。"],
                evidence={
                    "duplicate_place_name": duplicate.name,
                    "duplicate_place_id": duplicate.place_id or "",
                },
            )
            return WorkflowResult(
                status=WorkflowStatus.AWAITING_DUPLICATE_DECISION,
                day_state=self.current_day,
                conflict=conflict,
            )

        return self._route_and_pace_then_complete()

    def _compute_route_or_retry(self, place_ids: list[str]) -> RouteResult | WorkflowResult:
        try:
            return self.routes.compute_daily_route(place_ids, route_mode=self.trip_spec.route_mode)
        except RouteUnavailable as error:
            retries = self.current_day.retry_count + 1
            self._day_constraints.append(f"REJECT_ROUTE:{error.reason}")
            self._pending_candidates = []  # Discard cache; get fresh candidates on retry
            self.current_day.retry_count = retries
            self.current_day.warnings.append("ROUTE_UNAVAILABLE")
            self.tracer.event(
                "route_unavailable",
                {"day": self.current_day.day, "reason": error.reason, "retry": retries},
            )
            if retries >= 2:
                self.current_day.status = DayPlanStatus.NEEDS_MANUAL_REVIEW
                return WorkflowResult(
                    status=WorkflowStatus.NEEDS_MANUAL_REVIEW,
                    day_state=self.current_day,
                )
            return self.start_day(self.current_day.day)

    def _route_and_pace_then_complete(self) -> WorkflowResult:
        hotel = self.current_day.leg_hotel or self.trip_spec.hotel
        # ── Short-circuit: if the user has already accepted an intensive day
        # for this planning cycle, skip ALL subsequent pace gates so the
        # system never double-intercepts the same user decision (Task 3). ──
        _pace_accepted = "USER_ACCEPTED_INTENSIVE_DAY" in self.current_day.warnings

        self._emit("🚗 Route API 計算景點間移動時間…")
        route_result = self._compute_route_or_retry(
            [hotel.place_id]
            + [place.place_id for place in self.current_day.places]
            + [hotel.place_id]
        )
        if isinstance(route_result, WorkflowResult):
            return route_result

        self.current_day.route = route_result.evidence
        initial = evaluate_pace(
            self.current_day.places,
            route_result.evidence,
            self.trip_spec.pace,
            walking_preference=self.trip_spec.walking_preference,
        )
        if not _pace_accepted and initial.status is PaceStatus.CONFLICT:
            return self._pause_for_pace(initial.reasons, "initial_route")

        # ── Task 3: Food Agent — lunch + dinner, with budget & traveller context ──
        self._emit("🍽️ 美食 Agent 正在挑選午餐與晚餐…")
        food_proposal = self.agents.propose_food(
            self.current_day.places,
            remaining_budget=self.global_memory.remaining_budget,
            traveler_type=self.trip_spec.traveler_type,
        )
        meal_stops: list[PlaceStop] = []

        # Fill LUNCH_PLACEHOLDER / DINNER_PLACEHOLDER slots with real restaurants.
        # When no placeholder slots exist (old-style fixture output) fall back to
        # the original append-only logic so all existing tests keep passing.
        has_placeholders = any(
            s.slot_type in (SlotType.LUNCH_PLACEHOLDER, SlotType.DINNER_PLACEHOLDER)
            for s in self.current_day.timeslots
        )
        if has_placeholders:
            resolved: list[TimeSlot] = []
            lunch_idx, dinner_idx = 0, 0
            for slot in self.current_day.timeslots:
                if slot.slot_type is SlotType.LUNCH_PLACEHOLDER:
                    candidates = food_proposal.lunch_candidates
                    if lunch_idx < len(candidates):
                        restaurant = candidates[lunch_idx]
                        lunch_idx += 1
                        try:
                            meal_stops.append(self.places.ground(restaurant))
                            resolved.append(
                                TimeSlot(
                                    start_time=slot.start_time,
                                    end_time=slot.end_time,
                                    slot_type=SlotType.LUNCH,
                                    location_name=restaurant,
                                    notes=slot.notes,
                                )
                            )
                        except GroundingNotFound:
                            resolved.append(slot)  # keep placeholder if grounding fails
                    else:
                        resolved.append(slot)
                elif slot.slot_type is SlotType.DINNER_PLACEHOLDER:
                    candidates = food_proposal.dinner_candidates
                    if dinner_idx < len(candidates):
                        restaurant = candidates[dinner_idx]
                        dinner_idx += 1
                        try:
                            meal_stops.append(self.places.ground(restaurant))
                            resolved.append(
                                TimeSlot(
                                    start_time=slot.start_time,
                                    end_time=slot.end_time,
                                    slot_type=SlotType.DINNER,
                                    location_name=restaurant,
                                    notes=slot.notes,
                                )
                            )
                        except GroundingNotFound:
                            resolved.append(slot)
                    else:
                        resolved.append(slot)
                else:
                    resolved.append(slot)
            self.current_day.timeslots = resolved
        else:
            # Backward-compat: no PLACEHOLDER slots → old-style meal handling
            for name in food_proposal.lunch_candidates[:1]:
                meal_stops.append(self.places.ground(name))
            for name in food_proposal.dinner_candidates[:1]:
                try:
                    meal_stops.append(self.places.ground(name))
                except GroundingNotFound:
                    pass  # Dinner grounding failure is non-fatal; skip silently

        self.current_day.meals = meal_stops
        self._emit("🚗 Route API 驗證含餐廳的完整路線…")
        full_route_result = self._compute_route_or_retry(
            [hotel.place_id]
            + [place.place_id for place in self.current_day.places + self.current_day.meals]
            + [hotel.place_id]
        )
        if isinstance(full_route_result, WorkflowResult):
            return full_route_result

        self.current_day.route = full_route_result.evidence
        final = evaluate_pace(
            self.current_day.places,
            full_route_result.evidence,
            self.trip_spec.pace,
            walking_preference=self.trip_spec.walking_preference,
        )
        if not _pace_accepted and final.status is PaceStatus.CONFLICT:
            return self._pause_for_pace(final.reasons, "restaurant_included_final_route")

        # ── TimeSlot gap validation ────────────────────────────────────────────
        # Verify that the time buffers in the day's schedule are physically
        # achievable given the route's worst-case single-leg transfer time.
        max_transfer = full_route_result.evidence.max_single_transfer_minutes
        if self.current_day.timeslots and max_transfer > 0:
            gap_reasons = _check_schedule_gaps(self.current_day.timeslots, max_transfer)
            if gap_reasons:
                return self._pause_for_pace(gap_reasons, "schedule_timing_conflict")

        self.current_day.prices = self.price_collector.collect(self.current_day, full_route_result)
        return self._run_budget_gate()

    def _pause_for_pace(self, reasons: list[str], phase: str) -> WorkflowResult:
        # ── Task 2: Auto-retry with next candidate before pausing the user ──
        if self._pending_candidates:
            self.tracer.event(
                "pace_candidate_fallback",
                {
                    "day": self.current_day.day,
                    "phase": phase,
                    "remaining_candidates": len(self._pending_candidates),
                },
            )
            return self.start_day(self.current_day.day)

        # ── 瓶頸路段分析 ────────────────────────────────────────────────────
        # 找出耗時最長的單一路段，傳入 evidence 讓 UI 向使用者說明
        # 「哪一段路花最多時間」，提供可操作的重新規劃建議。
        # 停靠點序列與計算路線時使用的 place_id 列表相同：
        #   initial_route            → hotel + places + hotel（meals 為空）
        #   restaurant_included_…  → hotel + places + meals + hotel
        bottleneck_evidence: dict[str, object] = {}
        if (
            self.current_day
            and self.current_day.route
            and self.current_day.route.legs
        ):
            worst = max(self.current_day.route.legs, key=lambda leg: leg.duration_minutes)
            worst_idx = self.current_day.route.legs.index(worst)
            hotel = self.current_day.leg_hotel or self.trip_spec.hotel
            # intermediate stops = places + meals (meals is [] for initial_route phase)
            intermediate = self.current_day.places + self.current_day.meals
            all_stops = [hotel] + intermediate + [hotel]
            if worst_idx + 1 < len(all_stops):
                bottleneck_evidence = {
                    "bottleneck_orig": all_stops[worst_idx].name,
                    "bottleneck_dest": all_stops[worst_idx + 1].name,
                    "bottleneck_minutes": worst.duration_minutes,
                }

        conflict = ConflictEvent(
            conflict_type=ConflictType.PACE_EXCEEDED,
            day=self.current_day.day,
            reasons=reasons,
            evidence={
                "phase": phase,
                "route": self.current_day.route.model_dump(),
                **bottleneck_evidence,
            },
        )
        self.tracer.event(
            "pace_conflict",
            {"day": self.current_day.day, "reasons": reasons, "phase": phase},
        )
        return WorkflowResult(
            status=WorkflowStatus.AWAITING_PACE_DECISION,
            day_state=self.current_day,
            conflict=conflict,
        )

    def _run_budget_gate(self) -> WorkflowResult:
        self._emit("💰 預算閘口：核算本日費用…")
        budget_limit = (
            self.trip_spec.budget_override_history[-1]
            if self.trip_spec.budget_override_history
            else self.trip_spec.budget_amount
        )
        outcome = evaluate_budget(
            self.trip_spec.prices + self.current_day.prices,
            self.trip_spec.fx_snapshot,
            budget_limit,
        )
        if outcome.status is not BudgetStatus.PASSED:
            conflict_type = (
                ConflictType.PRICE_MISSING
                if outcome.status is BudgetStatus.MISSING_PRICE
                else ConflictType.BUDGET_EXCEEDED
            )
            conflict = ConflictEvent(
                conflict_type=conflict_type,
                day=self.current_day.day,
                reasons=[outcome.status.value],
                evidence=outcome.model_dump(),
            )
            self.tracer.event(
                "budget_conflict",
                {
                    "day": self.current_day.day,
                    "status": outcome.status.value,
                    "missing_item_ids": outcome.missing_item_ids,
                },
            )
            return WorkflowResult(
                status=(
                    WorkflowStatus.AWAITING_PRICE_DECISION
                    if outcome.status is BudgetStatus.MISSING_PRICE
                    else WorkflowStatus.AWAITING_BUDGET_DECISION
                ),
                day_state=self.current_day,
                conflict=conflict,
            )
        return self._review_and_commit_without_replanning()

    def _review_and_commit_without_replanning(self) -> WorkflowResult:
        self._emit("🧐 審查 Agent 進行最終品質把關…")
        review = self.reviewer.review(self.current_day)
        self.current_day.quality_score = review.score
        self.current_day.warnings.extend(review.warnings)

        # ── Task 4: Reviewer veto — score ≤ 2 triggers automatic replan ──
        if review.score <= 2:
            # ── Hard override (Task 2): a traveller who explicitly accepted a
            # tiring day has made an informed decision that the AI must honour
            # unconditionally.  The Reviewer Agent cannot veto that choice.
            # Emit a transparency note and fall through to DAY_APPROVED. ───
            if "USER_ACCEPTED_INTENSIVE_DAY" in self.current_day.warnings:
                self._emit("⚠️ Reviewer 給了低分，但使用者已手動批准，強制放行！")
                # Do NOT return — fall through to the approval block below.
            else:
                retry_count = self.current_day.retry_count + 1
                self.current_day.retry_count = retry_count
                self.tracer.event(
                    "reviewer_veto",
                    {"day": self.current_day.day, "score": review.score, "retry": retry_count},
                )
                if retry_count < 2:
                    # Inject reviewer feedback as a day-scoped constraint and retry
                    self._day_constraints.append(f"REVIEWER_VETO:{review.summary}")
                    self._pending_candidates = []  # Discard cache; regenerate fresh
                    return self.start_day(self.current_day.day)
                # Retry budget exhausted — surface veto reason in warnings
                # (Task 1: UI reads this to display the root cause to the user)
                # and hand off to manual review.
                self.current_day.warnings.append(f"REVIEWER_VETO:{review.summary}")
                self.current_day.status = DayPlanStatus.NEEDS_MANUAL_REVIEW
                self.tracer.event(
                    "reviewer_veto_escalated",
                    {"day": self.current_day.day, "score": review.score},
                )
                return WorkflowResult(
                    status=WorkflowStatus.NEEDS_MANUAL_REVIEW,
                    day_state=self.current_day,
                )

        self.current_day.status = DayPlanStatus.APPROVED
        self.approved_days.append(self.current_day)
        self._update_global_memory_after_approval()
        self.tracer.event("day_state_committed", self.current_day.model_dump(mode="json"))
        return WorkflowResult(status=WorkflowStatus.DAY_APPROVED, day_state=self.current_day)

    def _update_global_memory_after_approval(self) -> None:
        """Sync global memory after a day is committed."""
        # Accumulate visited place names (avoid duplicates in the list)
        for place in self.current_day.places + self.current_day.meals:
            if place.name not in self.global_memory.visited_places:
                self.global_memory.visited_places.append(place.name)

        # Recompute remaining budget from all confirmed costs so far
        if self.trip_spec.fx_snapshot is not None:
            all_prices = list(self.trip_spec.prices)
            for day_state in self.approved_days:
                all_prices.extend(day_state.prices)
            budget_limit = (
                self.trip_spec.budget_override_history[-1]
                if self.trip_spec.budget_override_history
                else self.trip_spec.budget_amount
            )
            from travel_planner.validation.budget import evaluate_budget
            outcome = evaluate_budget(all_prices, self.trip_spec.fx_snapshot, budget_limit)
            self.global_memory.remaining_budget = max(
                Decimal("0"), budget_limit - outcome.confirmed_total
            )

    def resume(
        self,
        decision: UserDecision,
        progress_callback: Callable[[str], None] | None = None,
        metrics_callback: Callable[[str, int, int, float], None] | None = None,
    ) -> WorkflowResult:
        if progress_callback is not None:
            self._progress_cb = progress_callback
        if metrics_callback is not None:
            self._metrics_cb = metrics_callback
        # Distribute metrics callback to every LLM inside the agent suite
        _set_mcb = getattr(self.agents, "set_metrics_callback", None)
        if _set_mcb is not None:
            _set_mcb(self._metrics_cb)
        # ── Phase 1: macro decision — no current_day exists yet ───────────
        if decision.choice is UserChoice.PROCEED_WITH_WARNINGS:
            # User acknowledges the feasibility warnings and starts micro-planning
            self.tracer.event("user_decision", {"phase": "macro", "choice": decision.choice.value})
            self.current_day = None
            return self.start_day(1)

        self.tracer.event(
            "user_decision",
            {"day": self.current_day.day, "choice": decision.choice.value},
        )
        if decision.choice is UserChoice.INCREASE_BUDGET_KEEP_PLAN:
            if decision.new_budget_limit is None:
                raise ValueError("new_budget_limit is required for a budget increase")
            self.trip_spec.add_budget_override(decision.new_budget_limit)
            self.current_day.warnings.append("USER_APPROVED_BUDGET_INCREASE")
            return self._review_and_commit_without_replanning()
        if decision.choice is UserChoice.KEEP_PACE_REPLAN:
            # ── Data-driven leg feedback (Task 1) ─────────────────────────
            # Extract the worst-case leg from the current route so the LLM
            # receives an actionable REJECT_ROUTE constraint naming the exact
            # origin/destination pair that is too far, rather than a vague
            # "KEEP_PACE_LIMITS" string it cannot act on.
            constraint = "KEEP_PACE_LIMITS"
            if (
                self.current_day
                and self.current_day.route
                and self.current_day.route.legs
            ):
                worst = max(
                    self.current_day.route.legs, key=lambda l: l.duration_minutes
                )
                worst_idx = self.current_day.route.legs.index(worst)
                all_stops = (
                    [self.current_day.leg_hotel or self.trip_spec.hotel]
                    + self.current_day.places
                )
                if worst_idx + 1 < len(all_stops):
                    orig_name = all_stops[worst_idx].name
                    dest_name = all_stops[worst_idx + 1].name
                    constraint = (
                        f"REJECT_ROUTE: 從「{orig_name}」到「{dest_name}」"
                        f"移動時間約 {worst.duration_minutes} 分鐘，超出合理範圍。"
                        f"請刪除「{dest_name}」，改選距離「{orig_name}」60 分鐘內的景點。"
                    )
            self._day_constraints = [constraint, "CONCENTRATE_AREA"]
            self._pending_candidates = []  # Full user-initiated replan
            self.pending_warnings.append("PACE_REPLANNED")
            # ── Reset retry count (Task 3/4: user-initiated replan gets a
            # fresh budget so the reviewer veto cap cannot block it
            # immediately) ───────────────────────────────────────────────
            if self.current_day is not None:
                self.current_day.retry_count = 0
            return self.start_day(self.current_day.day)
        if decision.choice is UserChoice.ACCEPT_PACE_WARNING:
            self.current_day.warnings.append("USER_ACCEPTED_INTENSIVE_DAY")
            return self._run_budget_gate()
        if decision.choice is UserChoice.INCREASE_DAY_PACE:
            if decision.new_pace is None:
                raise ValueError("new_pace is required for a pace increase")
            self.trip_spec.pace = decision.new_pace
            return self._route_and_pace_then_complete()
        if decision.choice in {
            UserChoice.KEEP_LOCKED_REDUCE_COST,
            UserChoice.REPLACE_ITEMS_KEEP_BUDGET,
        }:
            self._day_constraints = [decision.choice.value]
            self._pending_candidates = []  # Full user-initiated replan
            return self.start_day(self.current_day.day)
        if decision.choice is UserChoice.ACCEPT_COST_WARNING:
            self.current_day.warnings.append("UNVERIFIED_COST_ACCEPTED")
            return self._review_and_commit_without_replanning()

        # ── Phase 2: duplicate-place decision ─────────────────────────────
        if decision.choice is UserChoice.ALLOW_REVISIT:
            if self._pending_duplicate_name:
                self.global_memory.allowed_duplicates.append(self._pending_duplicate_name)
                self.tracer.event(
                    "duplicate_allowed",
                    {"place": self._pending_duplicate_name, "day": self.current_day.day},
                )
                self._pending_duplicate_name = None
            return self._route_and_pace_then_complete()

        if decision.choice is UserChoice.REJECT_DUPLICATE:
            rejected_name = self._pending_duplicate_name or ""
            self._pending_duplicate_name = None
            self._permanent_rejections.append(f"REJECT_PLACE:{rejected_name}")
            self._pending_candidates = []  # Discard cache after user rejection
            self.tracer.event(
                "duplicate_rejected",
                {"place": rejected_name, "day": self.current_day.day},
            )
            return self.start_day(self.current_day.day)

        raise ValueError(f"Unsupported user decision: {decision.choice}")
