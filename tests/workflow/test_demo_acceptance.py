from decimal import Decimal

from travel_planner.agents.runner import FoodProposal
from travel_planner.domain.models import PriceRecord, PriceStatus, UserChoice, UserDecision
from travel_planner.workflow.orchestrator import (
    DemoFixtures,
    FakeRouteScenario,
    TravelWorkflow,
    WorkflowStatus,
    _FixturePlaces,
    _FixturePriceCollector,
    _FixtureReviewer,
    _FixtureRoutes,
    _FixtureTracer,
)


class CountingAgents:
    def __init__(self):
        self.itinerary_calls = 0
        self._itineraries = iter(
            [
                ["Osaka Castle", "Kaiyukan"],
                ["Dotonbori", "Shinsekai"],
            ]
        )

    def propose_itinerary(self, destination, pace_name, visited, rejected):
        self.itinerary_calls += 1

        class Proposal:
            def __init__(self, candidates):
                self.candidates = candidates

        return Proposal(candidates=[next(self._itineraries)])

    def propose_food(self, places):
        return FoodProposal(lunch_candidates=["Dotonbori Lunch"], dinner_candidates=[])


def build_scripted_demo_workflow() -> tuple[TravelWorkflow, CountingAgents]:
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
    agents = CountingAgents()
    workflow = TravelWorkflow(
        trip_spec=trip_spec,
        agents=agents,
        places=_FixturePlaces(),
        routes=_FixtureRoutes(
            [
                FakeRouteScenario(total_minutes=142, max_single_minutes=56, walking_km=4.2),
                FakeRouteScenario(total_minutes=68, max_single_minutes=28, walking_km=3.1),
                FakeRouteScenario(total_minutes=70, max_single_minutes=28, walking_km=3.4),
            ]
        ),
        price_collector=_FixturePriceCollector(
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
        reviewer=_FixtureReviewer(),
        tracer=_FixtureTracer(),
    )
    return workflow, agents


def test_demo_shows_pace_replan_then_budget_override_without_second_replan():
    workflow, agents = build_scripted_demo_workflow()

    pace_conflict = workflow.start_day(2)
    assert pace_conflict.status is WorkflowStatus.AWAITING_PACE_DECISION
    assert agents.itinerary_calls == 1

    budget_conflict = workflow.resume(UserDecision(choice=UserChoice.KEEP_PACE_REPLAN))
    assert budget_conflict.status is WorkflowStatus.AWAITING_BUDGET_DECISION
    assert agents.itinerary_calls == 2
    route_before = budget_conflict.day_state.route

    approved = workflow.resume(
        UserDecision(
            choice=UserChoice.INCREASE_BUDGET_KEEP_PLAN,
            new_budget_limit=Decimal("28000"),
        )
    )

    assert approved.status is WorkflowStatus.DAY_APPROVED
    assert approved.day_state.route == route_before
    assert agents.itinerary_calls == 2
    assert "USER_APPROVED_BUDGET_INCREASE" in approved.day_state.warnings
