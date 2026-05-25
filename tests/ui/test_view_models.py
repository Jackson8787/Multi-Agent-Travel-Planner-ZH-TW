import pytest
from pydantic import HttpUrl, SecretStr

from travel_planner.integrations.google_places import GroundingNotFound
from travel_planner.ui.app import (
    _build_must_visit_preview_queries,
    _build_trip_spec_from_preflight,
    _can_plan_next_day,
    _format_manual_review_reason,
    _workflow_step_statuses,
    _select_effective_hotel,
    _build_user_input_error,
    _validate_trip_spec_inputs,
    _validate_trip_submission_inputs,
    format_pace_conflict,
    format_price_source,
)
from travel_planner.config import Settings
from travel_planner.domain.models import (
    DayPlanState,
    DayPlanStatus,
    PlaceStop,
    RouteEvidence,
    RouteMode,
    WalkingPreference,
)
from travel_planner.domain.pace import PaceLevel
from travel_planner.workflow.orchestrator import WorkflowResult, WorkflowStatus


def test_pace_conflict_displays_observed_and_selected_limit():
    message = format_pace_conflict(observed_minutes=142, limit_minutes=90)

    assert "142" in message
    assert "90" in message
    assert "悠閒" in message


def test_price_source_displays_original_currency_and_provider():
    text = format_price_source("JPY 8,600", "Universal Studios Japan Official Website")

    assert "JPY 8,600" in text
    assert "Universal Studios Japan Official Website" in text


def test_validate_trip_spec_inputs_rejects_blank_hotel_name():
    with pytest.raises(ValueError, match="住宿名稱不能空白"):
        _validate_trip_spec_inputs(
            destination="橫濱",
            hotel_name="   ",
            must_visit_name="北韓船",
            must_visit_price="",
        )


def test_validate_trip_spec_inputs_requires_must_visit_name_when_price_is_provided():
    with pytest.raises(ValueError, match="填寫必去景點價格前，請先輸入必去景點名稱"):
        _validate_trip_spec_inputs(
            destination="橫濱",
            hotel_name="Yokohama Royal Park Hotel",
            must_visit_name="  ",
            must_visit_price="8600",
        )


def test_build_user_input_error_reports_unknown_place():
    message = _build_user_input_error(
        GroundingNotFound("北韓船"),
        field_label="必去景點",
        query="北韓船",
    )

    assert "必去景點" in message
    assert "北韓船" in message
    assert "找不到" in message


def test_build_must_visit_preview_queries_splits_lines_and_commas():
    queries = _build_must_visit_preview_queries("鋼彈工廠, 紅磚倉庫\n杯麵博物館")

    assert queries == ["鋼彈工廠", "紅磚倉庫", "杯麵博物館"]


def test_validate_trip_submission_inputs_requires_selected_hotel():
    with pytest.raises(ValueError, match="請先從住宿候補中選擇一間住宿。"):
        _validate_trip_submission_inputs(
            destination="橫濱",
            days=2,
            budget_amount="25000",
            lodging_budget_amount="8000",
            selected_hotel_place_id=None,
            must_visit_name="",
            must_visit_price="",
        )


def test_build_trip_spec_from_preflight_uses_selected_hotel_and_pre_grounded_must_visit(monkeypatch):
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
        destination="橫濱",
        days=2,
        budget_amount="25000",
        budget_currency="TWD",
        interests="anime, food",
        pace_level=PaceLevel.RELAXED,
        route_mode=RouteMode.AUTO,
        walking_preference=WalkingPreference.NORMAL,
        selected_hotel=PlaceStop(name="Hotel A", place_id="h1"),
        grounded_must_visit=[PlaceStop(name="Cup Noodles Museum", place_id="p1")],
        must_visit_name="Cup Noodles Museum",
        must_visit_price="0",
        must_visit_price_url="",
    )

    assert trip_spec.hotel.place_id == "h1"
    assert [stop.place_id for stop in trip_spec.must_visit] == ["p1"]
    assert trip_spec.route_mode is RouteMode.AUTO
    assert trip_spec.walking_preference is WalkingPreference.NORMAL


def test_select_effective_hotel_prefers_manual_hotel():
    candidate = PlaceStop(name="Candidate Hotel", place_id="candidate-id")
    manual = PlaceStop(name="Manual Hotel", place_id="manual-id")

    selected = _select_effective_hotel(candidate_hotel=candidate, manual_hotel=manual)

    assert selected.place_id == "manual-id"


def test_workflow_step_statuses_mark_route_failure_for_manual_review():
    result = WorkflowResult(
        status=WorkflowStatus.NEEDS_MANUAL_REVIEW,
        day_state=DayPlanState(
            day=1,
            status=DayPlanStatus.NEEDS_MANUAL_REVIEW,
            places=[PlaceStop(name="橫濱地標大廈", place_id="p1")],
            warnings=["ROUTE_UNAVAILABLE"],
            route=RouteEvidence(
                total_required_transfer_minutes=43,
                max_single_transfer_minutes=17,
                walking_distance_km=2.9,
            ),
        ),
    )

    statuses = _workflow_step_statuses(result)

    assert statuses["行程 Agent"] == "完成"
    assert statuses["Places 驗證"] == "完成"
    assert statuses["Routes 驗證"] == "失敗"
    assert statuses["Budget Gate"] == "待執行"
    assert statuses["檢查 Agent"] == "待執行"


def test_format_manual_review_reason_explains_route_unavailable():
    result = WorkflowResult(
        status=WorkflowStatus.NEEDS_MANUAL_REVIEW,
        day_state=DayPlanState(
            day=1,
            status=DayPlanStatus.NEEDS_MANUAL_REVIEW,
            places=[PlaceStop(name="橫濱地標大廈", place_id="p1")],
            warnings=["ROUTE_UNAVAILABLE"],
        ),
    )

    message = _format_manual_review_reason(result)

    assert "路線驗證" in message
    assert "兩次" in message


def test_format_manual_review_reason_explains_grounding_failure():
    result = WorkflowResult(
        status=WorkflowStatus.NEEDS_MANUAL_REVIEW,
        day_state=DayPlanState(
            day=1,
            status=DayPlanStatus.NEEDS_MANUAL_REVIEW,
            warnings=["GROUNDING_FAILED"],
        ),
    )

    message = _format_manual_review_reason(result)

    assert "景點" in message
    assert "解析" in message


def test_can_plan_next_day_only_when_not_on_last_day():
    assert _can_plan_next_day(current_day=1, total_days=3) is True
    assert _can_plan_next_day(current_day=3, total_days=3) is False
