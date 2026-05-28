from decimal import Decimal

from travel_planner.domain.models import DestinationLeg, PlaceStop
from travel_planner.domain.models import UserChoice, UserDecision
from travel_planner.workflow.orchestrator import DemoFixtures, TravelWorkflow, WorkflowStatus


def test_pace_conflict_pauses_then_replans_with_constraints():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())

    state = workflow.start_day(2)

    assert state.status is WorkflowStatus.AWAITING_PACE_DECISION
    replanned = workflow.resume(UserDecision(choice=UserChoice.KEEP_PACE_REPLAN))
    assert replanned.day_state.route.total_required_transfer_minutes <= 90
    assert "PACE_REPLANNED" in replanned.day_state.warnings


def test_increase_budget_keeps_route_and_moves_to_review():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.possible_budget_overrun())
    blocked = workflow.start_day(2)
    route_before = blocked.day_state.route

    completed = workflow.resume(
        UserDecision(
            choice=UserChoice.INCREASE_BUDGET_KEEP_PLAN,
            new_budget_limit=Decimal("28000"),
        )
    )

    assert completed.status is WorkflowStatus.DAY_APPROVED
    assert completed.day_state.route == route_before
    assert "USER_APPROVED_BUDGET_INCREASE" in completed.day_state.warnings


def test_restaurant_detour_can_trigger_final_pace_conflict():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.restaurant_causes_detour())

    result = workflow.start_day(1)

    assert result.status is WorkflowStatus.AWAITING_PACE_DECISION
    assert result.conflict.evidence["phase"] == "restaurant_included_final_route"


def test_second_unroutable_candidate_requires_manual_review():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.routes_fail_twice())

    result = workflow.start_day(3)

    assert result.status is WorkflowStatus.NEEDS_MANUAL_REVIEW
    assert result.day_state.retry_count == 2


def test_locked_must_visit_places_are_used_before_agent_candidates():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())
    workflow.trip_spec.destination = "Yokohama"
    workflow.trip_spec.must_visit = [
        PlaceStop(name="Cup Noodles Museum Yokohama", place_id="cup-yokohama", locked=True)
    ]

    result = workflow.start_day(1)

    assert result.day_state.places[0].place_id == "cup-yokohama"
    assert len(result.day_state.places) <= workflow.trip_spec.pace.max_major_places_per_day


def test_approved_day_is_recorded_in_workflow_approved_days():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())

    result1 = workflow.start_day(1)
    assert result1.status is WorkflowStatus.AWAITING_PACE_DECISION
    assert len(workflow.approved_days) == 0

    approved1 = workflow.resume(UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING))
    assert approved1.status is WorkflowStatus.DAY_APPROVED
    assert len(workflow.approved_days) == 1
    assert workflow.approved_days[0].day == 1


def test_multi_city_legs_route_each_day_to_correct_city():
    """Days are routed to the right leg's city and hotel when legs are configured."""
    from travel_planner.agents.runner import FoodProposal, ReviewResult

    captured_destinations: list[str] = []
    itinerary_sequences = iter([["Osaka Castle"], ["Kinkaku-ji"], ["Nishiki Market"]])

    class CapturingAgents:
        def propose_itinerary(self, destination, pace_name, visited, rejected, must_visit, remaining_slots, route_mode, walking_preference):
            captured_destinations.append(destination)
            class _Proposal:
                candidates = [next(itinerary_sequences)]
            return _Proposal()

        def propose_food(self, places, **kwargs):
            return FoodProposal(lunch_candidates=["Lunch"], dinner_candidates=[])

        def review(self, day_state):
            return ReviewResult(score=4, summary="ok", warnings=[])

    osaka_hotel = PlaceStop(name="Osaka Hotel", place_id="osaka-hotel")
    kyoto_hotel = PlaceStop(name="Kyoto Hotel", place_id="kyoto-hotel")

    fixtures = DemoFixtures.first_route_too_tiring()
    fixtures["agents"] = CapturingAgents()

    from travel_planner.workflow.orchestrator import FakeRouteScenario, _FixtureRoutes, _FixturePriceCollector, _FixtureReviewer
    fixtures["routes"] = _FixtureRoutes([
        FakeRouteScenario(total_minutes=60, max_single_minutes=30, walking_km=2.0),
        FakeRouteScenario(total_minutes=60, max_single_minutes=30, walking_km=2.0),
        FakeRouteScenario(total_minutes=60, max_single_minutes=30, walking_km=2.0),
        FakeRouteScenario(total_minutes=60, max_single_minutes=30, walking_km=2.0),
        FakeRouteScenario(total_minutes=60, max_single_minutes=30, walking_km=2.0),
        FakeRouteScenario(total_minutes=60, max_single_minutes=30, walking_km=2.0),
    ])
    fixtures["price_collector"] = _FixturePriceCollector([[], [], []])
    fixtures["reviewer"] = _FixtureReviewer()

    workflow = TravelWorkflow.from_fixtures(fixtures)
    # Replace single-city with two-city legs: Osaka 2 days, Kyoto 1 day
    workflow.trip_spec.legs = [
        DestinationLeg(city="Osaka", num_days=2, hotel=osaka_hotel),
        DestinationLeg(city="Kyoto", num_days=1, hotel=kyoto_hotel),
    ]

    # start_day → DAY_APPROVED (reviewer returns ok, budget passes → auto-committed)
    r1 = workflow.start_day(1)
    assert r1.status is WorkflowStatus.DAY_APPROVED
    assert r1.day_state.destination == "Osaka"
    assert r1.day_state.leg_hotel.place_id == "osaka-hotel"
    # _review_and_commit appends automatically; don't append manually
    assert len(workflow.approved_days) == 1

    r2 = workflow.start_day(2)
    assert r2.status is WorkflowStatus.DAY_APPROVED
    assert r2.day_state.destination == "Osaka"
    assert r2.day_state.leg_hotel.place_id == "osaka-hotel"
    assert len(workflow.approved_days) == 2

    r3 = workflow.start_day(3)
    assert r3.status is WorkflowStatus.DAY_APPROVED
    assert r3.day_state.destination == "Kyoto"
    assert r3.day_state.leg_hotel.place_id == "kyoto-hotel"
    assert len(workflow.approved_days) == 3

    assert captured_destinations == ["Osaka", "Osaka", "Kyoto"]


def test_validate_macro_passes_for_simple_single_city():
    """validate_macro always pauses for user confirmation even without critical issues."""
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())

    result = workflow.validate_macro()

    # Macro plan is always created and the workflow always pauses for confirmation
    assert workflow.macro_plan is not None
    assert not workflow.macro_plan.has_critical_issues
    assert result.status is WorkflowStatus.AWAITING_MACRO_DECISION
    # LLM city segments are populated by _FixtureAgents.propose_macro_plan
    assert len(workflow.macro_plan.city_segments) >= 1
    assert workflow.macro_plan.city_segments[0].city == "Osaka"

    # After user confirms, micro-planning begins (pace conflict in this fixture)
    result2 = workflow.resume(UserDecision(choice=UserChoice.PROCEED_WITH_WARNINGS))
    assert result2.status is WorkflowStatus.AWAITING_PACE_DECISION


def test_validate_macro_detects_must_visit_overload():
    """validate_macro pauses when must_visit count exceeds pace capacity."""
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())
    # Relaxed pace allows max 2 places/day; 1 day with 5 must_visit → overload
    workflow.trip_spec.days = 1
    workflow.trip_spec.must_visit = [
        PlaceStop(name=f"Place {i}", place_id=f"p{i}", locked=True) for i in range(5)
    ]

    result = workflow.validate_macro()

    assert result.status is WorkflowStatus.AWAITING_MACRO_DECISION
    assert workflow.macro_plan is not None
    assert workflow.macro_plan.has_critical_issues
    assert len(result.conflict.reasons) >= 1


def test_validate_macro_proceed_with_warnings_starts_planning():
    """PROCEED_WITH_WARNINGS after macro decision starts day 1 planning."""
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())
    workflow.trip_spec.days = 1
    workflow.trip_spec.must_visit = [
        PlaceStop(name=f"P{i}", place_id=f"p{i}", locked=True) for i in range(5)
    ]
    workflow.validate_macro()  # sets macro plan + AWAITING_MACRO_DECISION

    result = workflow.resume(UserDecision(choice=UserChoice.PROCEED_WITH_WARNINGS))

    # After proceeding, micro-planning begins (pace conflict from the fixture route)
    assert result.status is not WorkflowStatus.AWAITING_MACRO_DECISION


def test_global_memory_visited_places_updated_after_approval():
    """visited_places accumulates across approved days."""
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())
    assert workflow.global_memory.visited_places == []

    result = workflow.start_day(1)  # pace conflict
    assert result.status is WorkflowStatus.AWAITING_PACE_DECISION

    approved = workflow.resume(UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING))
    assert approved.status is WorkflowStatus.DAY_APPROVED

    # After approval, all confirmed place names are in visited_places
    committed_names = {p.name for p in approved.day_state.places + approved.day_state.meals}
    assert committed_names.issubset(set(workflow.global_memory.visited_places))


def _build_always_approve_fixtures(repeating_place: str) -> dict:
    """Return fixtures where Day 1 auto-approves (short route) and the agent always
    re-proposes ``repeating_place`` to trigger duplicate detection on Day 2."""
    from travel_planner.agents.runner import FoodProposal, ReviewResult
    from travel_planner.workflow.orchestrator import (
        FakeRouteScenario,
        _FixturePriceCollector,
        _FixtureReviewer,
        _FixtureRoutes,
    )

    class RepeatingAgents:
        def propose_itinerary(self, destination, pace_name, visited, rejected, must_visit, remaining_slots, route_mode, walking_preference):
            class _Proposal:
                candidates = [[repeating_place]]
            return _Proposal()

        def propose_food(self, places, **kwargs):
            return FoodProposal(lunch_candidates=["Dotonbori Lunch"], dinner_candidates=[])

        def review(self, day_state):
            return ReviewResult(score=4, summary="ok", warnings=[])

    fixtures = DemoFixtures.first_route_too_tiring()
    fixtures["agents"] = RepeatingAgents()
    fixtures["routes"] = _FixtureRoutes([
        FakeRouteScenario(total_minutes=30, max_single_minutes=15, walking_km=1.0),  # Day 1 places
        FakeRouteScenario(total_minutes=30, max_single_minutes=15, walking_km=1.0),  # Day 1 meals
        FakeRouteScenario(total_minutes=30, max_single_minutes=15, walking_km=1.0),  # Day 2 places
        FakeRouteScenario(total_minutes=30, max_single_minutes=15, walking_km=1.0),  # Day 2 meals
    ])
    fixtures["price_collector"] = _FixturePriceCollector([[], []])
    fixtures["reviewer"] = _FixtureReviewer()
    return fixtures


def test_duplicate_place_triggers_awaiting_duplicate_decision():
    """When an agent re-proposes a visited place, the workflow pauses for user input."""
    workflow = TravelWorkflow.from_fixtures(_build_always_approve_fixtures("Osaka Castle"))

    # Approve day 1; this puts "Osaka Castle" in visited_places
    r1 = workflow.start_day(1)
    assert r1.status is WorkflowStatus.DAY_APPROVED
    assert "Osaka Castle" in workflow.global_memory.visited_places

    # Day 2: agent proposes "Osaka Castle" again → duplicate detected
    r2 = workflow.start_day(2)
    assert r2.status is WorkflowStatus.AWAITING_DUPLICATE_DECISION
    assert r2.conflict.evidence["duplicate_place_name"] == "Osaka Castle"
    assert workflow._pending_duplicate_name == "Osaka Castle"


def test_allow_revisit_adds_to_allowed_duplicates_and_continues():
    """ALLOW_REVISIT whitelists the place and resumes routing."""
    workflow = TravelWorkflow.from_fixtures(_build_always_approve_fixtures("Osaka Castle"))

    workflow.start_day(1)   # approves, adds Osaka Castle to visited
    workflow.start_day(2)   # detects duplicate → AWAITING_DUPLICATE_DECISION

    result = workflow.resume(UserDecision(choice=UserChoice.ALLOW_REVISIT))

    assert "Osaka Castle" in workflow.global_memory.allowed_duplicates
    assert workflow._pending_duplicate_name is None
    # Workflow continues past duplicate gate
    assert result.status is not WorkflowStatus.AWAITING_DUPLICATE_DECISION


def test_reject_duplicate_replans_without_the_place():
    """REJECT_DUPLICATE re-runs start_day with the duplicate added to reject constraints."""
    from travel_planner.agents.runner import FoodProposal, ReviewResult
    from travel_planner.workflow.orchestrator import FakeRouteScenario, _FixturePriceCollector, _FixtureRoutes

    propose_calls: list[dict] = []

    class TrackingAgents:
        def __init__(self):
            # Day1→"Osaka Castle", Day2 first→"Osaka Castle" (dup), Day2 retry→"Dotonbori"
            self._plans = iter([["Osaka Castle"], ["Osaka Castle"], ["Dotonbori"]])

        def propose_itinerary(self, destination, pace_name, visited, rejected, must_visit, remaining_slots, route_mode, walking_preference):
            propose_calls.append({"rejected": list(rejected)})
            class _Proposal:
                candidates = [next(self._plans)]
            return _Proposal()

        def propose_food(self, places, **kwargs):
            return FoodProposal(lunch_candidates=["Lunch"], dinner_candidates=[])

        def review(self, day_state):
            return ReviewResult(score=4, summary="ok", warnings=[])

    short = FakeRouteScenario(total_minutes=30, max_single_minutes=15, walking_km=1.0)
    fixtures = DemoFixtures.first_route_too_tiring()
    fixtures["agents"] = TrackingAgents()
    # Day1-places, Day1-meals, Day2-retry-places, Day2-retry-meals
    fixtures["routes"] = _FixtureRoutes([short, short, short, short])
    fixtures["price_collector"] = _FixturePriceCollector([[], []])
    workflow = TravelWorkflow.from_fixtures(fixtures)

    r1 = workflow.start_day(1)
    assert r1.status is WorkflowStatus.DAY_APPROVED      # "Osaka Castle" committed

    r2 = workflow.start_day(2)
    assert r2.status is WorkflowStatus.AWAITING_DUPLICATE_DECISION

    result = workflow.resume(UserDecision(choice=UserChoice.REJECT_DUPLICATE))

    # Third propose_call had REJECT_PLACE:Osaka Castle in rejected list
    assert any("REJECT_PLACE:Osaka Castle" in c["rejected"] for c in propose_calls)
    assert result.status is WorkflowStatus.DAY_APPROVED
    assert any(p.name == "Dotonbori" for p in result.day_state.places)


def test_trip_spec_total_days_multi_city():
    trip_spec = DemoFixtures.trip_spec()
    assert trip_spec.total_days == trip_spec.days  # single-city falls back

    trip_spec.legs = [
        DestinationLeg(city="Osaka", num_days=3, hotel=PlaceStop(name="H1", place_id="h1")),
        DestinationLeg(city="Kyoto", num_days=2, hotel=PlaceStop(name="H2", place_id="h2")),
    ]
    assert trip_spec.total_days == 5


def test_previously_visited_places_passed_to_second_day_agent():
    from travel_planner.agents.runner import FoodProposal, ReviewResult

    captured_visited: list[list[str]] = []
    itinerary_sequences = iter([["Osaka Castle", "Kaiyukan"], ["Dotonbori", "Shinsekai"]])

    class TrackingAgents:
        def propose_itinerary(self, destination, pace_name, visited, rejected, must_visit, remaining_slots, route_mode, walking_preference):
            captured_visited.append(list(visited))
            class _Proposal:
                candidates = [next(itinerary_sequences)]
            return _Proposal()

        def propose_food(self, places, **kwargs):
            return FoodProposal(lunch_candidates=["Lunch"], dinner_candidates=[])

        def review(self, day_state):
            return ReviewResult(score=4, summary="ok", warnings=[])

    fixtures = DemoFixtures.first_route_too_tiring()
    fixtures["agents"] = TrackingAgents()
    workflow = TravelWorkflow.from_fixtures(fixtures)

    workflow.start_day(1)
    approved1 = workflow.resume(UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING))
    assert approved1.status is WorkflowStatus.DAY_APPROVED

    workflow.start_day(2)

    day1_all_names = {p.name for p in approved1.day_state.places + approved1.day_state.meals}
    assert len(captured_visited) == 2
    for name in day1_all_names:
        assert name in captured_visited[1], f"Expected {name!r} in day-2 visited list"


def test_pace_conflict_evidence_contains_bottleneck_when_legs_present():
    """When route.legs is populated, AWAITING_PACE_DECISION conflict.evidence
    must contain bottleneck_orig / bottleneck_dest / bottleneck_minutes so the
    UI can show the user which hop is causing the pace violation.
    """
    from travel_planner.agents.runner import FoodProposal, ReviewResult
    from travel_planner.domain.models import RouteEvidence, RouteLeg
    from travel_planner.integrations.google_routes import RouteResult
    from travel_planner.workflow.orchestrator import (
        _FixturePriceCollector,
        _FixtureReviewer,
        _FixtureTracer,
    )

    class LegsRoutes:
        """Returns a tiring route (total=180 min > RELAXED 90-min limit) WITH leg details."""

        def compute_daily_route(self, place_ids, *, route_mode=None):
            return RouteResult(
                evidence=RouteEvidence(
                    total_required_transfer_minutes=180,
                    max_single_transfer_minutes=95,
                    walking_distance_km=1.0,
                    legs=[
                        RouteLeg(duration_minutes=30, travel_mode="TRANSIT"),
                        RouteLeg(duration_minutes=95, travel_mode="TRANSIT"),  # worst leg
                        RouteLeg(duration_minutes=55, travel_mode="TRANSIT"),
                    ],
                )
            )

    class QuickAgents:
        def propose_itinerary(self, destination, pace_name, visited, rejected, must_visit, remaining_slots, route_mode, walking_preference):
            class _Proposal:
                candidates = ["Osaka Castle", "Kaiyukan"]
            return _Proposal()

        def propose_food(self, places, **kwargs):
            return FoodProposal(lunch_candidates=["Dotonbori Lunch"], dinner_candidates=[])

        def review(self, day_state):
            return ReviewResult(score=4, summary="ok", warnings=[])

    fixtures = DemoFixtures.first_route_too_tiring()
    fixtures["agents"] = QuickAgents()
    fixtures["routes"] = LegsRoutes()
    fixtures["price_collector"] = _FixturePriceCollector([[]])
    fixtures["reviewer"] = _FixtureReviewer()
    fixtures["tracer"] = _FixtureTracer()

    workflow = TravelWorkflow.from_fixtures(fixtures)
    result = workflow.start_day(1)

    assert result.status is WorkflowStatus.AWAITING_PACE_DECISION
    ev = result.conflict.evidence
    # Bottleneck keys must be present because LegsRoutes provides real legs
    assert "bottleneck_orig" in ev, "conflict.evidence missing bottleneck_orig"
    assert "bottleneck_dest" in ev, "conflict.evidence missing bottleneck_dest"
    assert ev["bottleneck_minutes"] == 95, (
        f"Expected worst leg=95 min, got {ev.get('bottleneck_minutes')}"
    )
