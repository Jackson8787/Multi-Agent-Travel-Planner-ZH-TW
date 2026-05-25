from travel_planner.ui.app import format_pace_conflict, format_price_source


def test_pace_conflict_displays_observed_and_selected_limit():
    message = format_pace_conflict(observed_minutes=142, limit_minutes=90)

    assert "142" in message
    assert "90" in message
    assert "悠閒" in message


def test_price_source_displays_original_currency_and_provider():
    text = format_price_source("JPY 8,600", "Universal Studios Japan Official Website")

    assert "JPY 8,600" in text
    assert "Universal Studios Japan Official Website" in text
