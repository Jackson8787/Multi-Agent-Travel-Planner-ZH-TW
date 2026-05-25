from decimal import Decimal

from travel_planner.domain.models import PlaceStop
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

        def propose_food(self, places):
            return FoodProposal(lunch_candidates=["Lunch"], dinner_candidates=[])

        def review(self, day_state):
            return ReviewResult(score=4, summary="ok", warnings=[])

    fixtures = DemoFixtures.first_route_too_tiring()
    fixtures["agents"] = TrackingAgents()
    workflow = TravelWorkflow.from_fixtures(fixtures)

    result1 = workflow.start_day(1)
    approved1 = workflow.resume(UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING))
    assert approved1.status is WorkflowStatus.DAY_APPROVED

    workflow.start_day(2)

    day1_all_names = {p.name for p in approved1.day_state.places + approved1.day_state.meals}
    assert len(captured_visited) == 2
    for name in day1_all_names:
        assert name in captured_visited[1], f"Expected {name!r} in day-2 visited list"
