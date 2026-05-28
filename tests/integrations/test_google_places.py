from travel_planner.integrations.google_places import GooglePlacesClient


def test_text_search_grounds_first_place_candidate(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={"places": [{"id": "place123", "displayName": {"text": "Dotonbori"}}]},
    )

    place = GooglePlacesClient("maps").ground("Dotonbori Osaka Japan")

    assert place.place_id == "place123"


def test_lookup_destination_returns_city_anchor(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={
            "places": [
                {
                    "id": "dest123",
                    "displayName": {"text": "Yokohama"},
                    "location": {"latitude": 35.4437, "longitude": 139.6380},
                }
            ]
        },
    )

    result = GooglePlacesClient("maps").lookup_destination("橫濱")

    assert result.name == "Yokohama"
    assert result.place_id == "dest123"


def test_search_hotel_candidates_returns_three_ranked_places(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={
            "places": [
                {"id": "h1", "displayName": {"text": "Hotel A"}},
                {"id": "h2", "displayName": {"text": "Hotel B"}},
                {"id": "h3", "displayName": {"text": "Hotel C"}},
                {"id": "h4", "displayName": {"text": "Hotel D"}},
            ]
        },
    )

    results = GooglePlacesClient("maps").search_hotel_candidates("橫濱", max_results=3)

    assert [hotel.place_id for hotel in results] == ["h1", "h2", "h3"]
