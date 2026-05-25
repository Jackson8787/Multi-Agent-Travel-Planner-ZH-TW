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
    ExchangeRateSnapshot,
    PlaceLoadTag,
    PlaceStop,
    PriceRecord,
    PriceStatus,
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


class WorkflowStatus(StrEnum):
    PLANNING = "PLANNING"
    AWAITING_PACE_DECISION = "AWAITING_PACE_DECISION"
    AWAITING_PRICE_DECISION = "AWAITING_PRICE_DECISION"
    AWAITING_BUDGET_DECISION = "AWAITING_BUDGET_DECISION"
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

    def propose_food(self, places):
        return FoodProposal(lunch_candidates=[next(self._lunches)], dinner_candidates=[])


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
        self.replanning_constraints: list[str] = []
        self.pending_warnings: list[str] = []

    @classmethod
    def from_fixtures(cls, fixtures: dict) -> "TravelWorkflow":
        return cls(**fixtures)

    def start_day(self, day: int) -> WorkflowResult:
        existing_retry_count = self.current_day.retry_count if self.current_day else 0
        locked_places = self.trip_spec.must_visit[: self.trip_spec.pace.max_major_places_per_day]
        remaining_slots = self.trip_spec.pace.max_major_places_per_day - len(locked_places)
        candidate_names: list[str] = []
        if remaining_slots > 0:
            candidate_names = self.agents.propose_itinerary(
                self.trip_spec.destination,
                self.trip_spec.pace.level.value,
                visited=[place.name for place in locked_places],
                rejected=self.replanning_constraints,
                must_visit=[place.name for place in locked_places],
                remaining_slots=remaining_slots,
                route_mode=self.trip_spec.route_mode.value,
                walking_preference=self.trip_spec.walking_preference.value,
            ).candidates[0]
        try:
            proposed_places: list[PlaceStop] = []
            for name in candidate_names:
                grounded = self.places.ground(name)
                if any(
                    existing.place_id == grounded.place_id or existing.name == grounded.name
                    for existing in [*locked_places, *proposed_places]
                ):
                    continue
                proposed_places.append(grounded)
                if len(proposed_places) >= remaining_slots:
                    break
            places = [*locked_places, *proposed_places]
        except GroundingNotFound as error:
            previous_retries = self.current_day.retry_count if self.current_day else 0
            retries = previous_retries + 1
            self.replanning_constraints.append(f"REJECT_PLACE:{error.query}")
            failed = DayPlanState(day=day, retry_count=retries, warnings=["GROUNDING_FAILED"])
            self.current_day = failed
            self.tracer.event("grounding_failed", {"day": day, "query": error.query, "retry": retries})
            if retries >= 2:
                return WorkflowResult(status=WorkflowStatus.NEEDS_MANUAL_REVIEW, day_state=failed)
            return self.start_day(day)
        self.current_day = DayPlanState(
            day=day,
            places=places,
            warnings=list(self.pending_warnings),
            retry_count=existing_retry_count,
            status=DayPlanStatus.VALIDATING,
        )
        self.pending_warnings = []
        return self._route_and_pace_then_complete()

    def _compute_route_or_retry(self, place_ids: list[str]) -> RouteResult | WorkflowResult:
        try:
            return self.routes.compute_daily_route(place_ids, route_mode=self.trip_spec.route_mode)
        except RouteUnavailable as error:
            retries = self.current_day.retry_count + 1
            self.replanning_constraints.append(f"REJECT_ROUTE:{error.reason}")
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
        route_result = self._compute_route_or_retry(
            [self.trip_spec.hotel.place_id]
            + [place.place_id for place in self.current_day.places]
            + [self.trip_spec.hotel.place_id]
        )
        if isinstance(route_result, WorkflowResult):
            return route_result

        self.current_day.route = route_result.evidence
        initial = evaluate_pace(self.current_day.places, route_result.evidence, self.trip_spec.pace)
        if initial.status is PaceStatus.CONFLICT:
            return self._pause_for_pace(initial.reasons, "initial_route")

        meal_names = self.agents.propose_food(self.current_day.places).lunch_candidates[:1]
        self.current_day.meals = [self.places.ground(name) for name in meal_names]
        full_route_result = self._compute_route_or_retry(
            [self.trip_spec.hotel.place_id]
            + [place.place_id for place in self.current_day.places + self.current_day.meals]
            + [self.trip_spec.hotel.place_id]
        )
        if isinstance(full_route_result, WorkflowResult):
            return full_route_result

        self.current_day.route = full_route_result.evidence
        final = evaluate_pace(
            self.current_day.places,
            full_route_result.evidence,
            self.trip_spec.pace,
        )
        if final.status is PaceStatus.CONFLICT:
            return self._pause_for_pace(final.reasons, "restaurant_included_final_route")
        self.current_day.prices = self.price_collector.collect(self.current_day, full_route_result)
        return self._run_budget_gate()

    def _pause_for_pace(self, reasons: list[str], phase: str) -> WorkflowResult:
        conflict = ConflictEvent(
            conflict_type=ConflictType.PACE_EXCEEDED,
            day=self.current_day.day,
            reasons=reasons,
            evidence={"phase": phase, "route": self.current_day.route.model_dump()},
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
        review = self.reviewer.review(self.current_day)
        self.current_day.quality_score = review.score
        self.current_day.warnings.extend(review.warnings)
        self.current_day.status = DayPlanStatus.APPROVED
        self.tracer.event("day_state_committed", self.current_day.model_dump(mode="json"))
        return WorkflowResult(status=WorkflowStatus.DAY_APPROVED, day_state=self.current_day)

    def resume(self, decision: UserDecision) -> WorkflowResult:
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
            self.replanning_constraints = ["KEEP_PACE_LIMITS", "CONCENTRATE_AREA"]
            self.pending_warnings.append("PACE_REPLANNED")
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
            self.replanning_constraints = [decision.choice.value]
            return self.start_day(self.current_day.day)
        if decision.choice is UserChoice.ACCEPT_COST_WARNING:
            self.current_day.warnings.append("UNVERIFIED_COST_ACCEPTED")
            return self._review_and_commit_without_replanning()
        raise ValueError(f"Unsupported user decision: {decision.choice}")
