from travel_planner.integrations.google_places import GooglePlacesClient


def test_text_search_grounds_first_place_candidate(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={"places": [{"id": "place123", "displayName": {"text": "Dotonbori"}}]},
    )

    place = GooglePlacesClient("maps").ground("Dotonbori Osaka Japan")

    assert place.place_id == "place123"
