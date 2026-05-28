from decimal import Decimal

from pydantic import HttpUrl, SecretStr

from travel_planner.agents.runner import FoodProposal
from travel_planner.config import Settings
from travel_planner.domain.models import PlaceStop, PriceRecord, PriceStatus, UserChoice, UserDecision
from travel_planner.domain.models import RouteMode, WalkingPreference
from travel_planner.domain.pace import PaceLevel
from travel_planner.ui.app import _build_trip_spec_from_preflight
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
        self.itinerary_calls += 1

        class Proposal:
            def __init__(self, candidates):
                self.candidates = candidates

        return Proposal(candidates=[next(self._itineraries)])

    def propose_food(self, places, **kwargs):
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


def test_preflight_trip_spec_keeps_selected_hotel_before_workflow(monkeypatch):
    settings = Settings.model_construct(
        google_maps_api_key=SecretStr("maps"),
        exchange_rate_api_key=SecretStr("rates"),
        azure_openai_api_key=SecretStr("azure"),
        azure_openai_endpoint=HttpUrl("https://example.openai.azure.com/"),
        azure_openai_deployment="gpt-5-mini",
        azure_openai_api_version="2024-10-21",
    )

    class _Snapshot:
        def model_dump(self):
            return {
                "provider": "ExchangeRate-API",
                "base_currency": "JPY",
                "target_currency": "TWD",
                "rate": "0.2",
                "retrieved_at": "2026-05-25T00:00:00",
                "rate_type": "INDICATIVE_MIDPOINT",
            }

    monkeypatch.setattr(
        "travel_planner.ui.app.ExchangeRateClient.snapshot",
        lambda self, base_currency, target_currency: _Snapshot(),
    )

    trip_spec = _build_trip_spec_from_preflight(
        settings=settings,
        destination="Osaka",
        days=3,
        budget_amount="25000",
        budget_currency="TWD",
        interests="anime, food",
        pace_level=PaceLevel.RELAXED,
        route_mode=RouteMode.AUTO,
        walking_preference=WalkingPreference.NORMAL,
        grounded_must_visit=[PlaceStop(name="Dotonbori", place_id="poi-1")],
        must_visit_name="Dotonbori",
        must_visit_price="0",
        must_visit_price_url="",
    )
    # Simulate macro-phase hotel selection: inject the chosen hotel directly
    trip_spec.hotel = PlaceStop(name="Hotel Monterey", place_id="hotel-1")

    workflow = TravelWorkflow(
        trip_spec=trip_spec,
        agents=CountingAgents(),
        places=_FixturePlaces(),
        routes=_FixtureRoutes([FakeRouteScenario(total_minutes=30, max_single_minutes=15, walking_km=1.2)]),
        price_collector=_FixturePriceCollector([[]]),
        reviewer=_FixtureReviewer(),
        tracer=_FixtureTracer(),
    )

    assert workflow.trip_spec.hotel.place_id == "hotel-1"
    assert workflow.trip_spec.must_visit[0].place_id == "poi-1"
