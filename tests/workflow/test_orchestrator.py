from decimal import Decimal

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
