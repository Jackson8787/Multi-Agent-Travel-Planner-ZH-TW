import pytest

from travel_planner.integrations.google_places import GroundingNotFound
from travel_planner.ui.app import (
    _build_must_visit_preview_queries,
    _build_user_input_error,
    _validate_trip_spec_inputs,
    _validate_trip_submission_inputs,
    format_pace_conflict,
    format_price_source,
)


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
