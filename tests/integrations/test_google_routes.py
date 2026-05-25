from travel_planner.integrations.google_routes import GoogleRoutesClient


def test_route_without_transit_fare_is_valid_but_has_no_verified_price(httpx_mock):
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "1380s",
                    "distanceMeters": 3000,
                    "polyline": {"encodedPolyline": "leg-1"},
                    "legs": [{"duration": "1380s"}],
                }
            ]
        },
    )
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "1740s",
                    "distanceMeters": 3200,
                    "polyline": {"encodedPolyline": "leg-2"},
                    "legs": [{"duration": "1740s"}],
                }
            ]
        },
    )

    route = GoogleRoutesClient("maps").compute_daily_route(["hotel", "poi", "hotel"])

    assert route.evidence.total_required_transfer_minutes == 52
    assert route.evidence.encoded_polyline_segments == ["leg-1", "leg-2"]
    assert route.transit_fare is None


def test_transit_route_with_multiple_stops_is_computed_per_leg(httpx_mock):
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "1200s",
                    "distanceMeters": 1800,
                    "polyline": {"encodedPolyline": "leg-1"},
                    "legs": [{"duration": "1200s"}],
                    "travelAdvisory": {
                        "transitFare": {"currencyCode": "JPY", "units": "230", "nanos": 0}
                    },
                }
            ]
        },
    )
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "1500s",
                    "distanceMeters": 2100,
                    "polyline": {"encodedPolyline": "leg-2"},
                    "legs": [{"duration": "1500s"}],
                    "travelAdvisory": {
                        "transitFare": {"currencyCode": "JPY", "units": "180", "nanos": 0}
                    },
                }
            ]
        },
    )

    route = GoogleRoutesClient("maps").compute_daily_route(["hotel", "poi", "hotel"])

    assert route.evidence.total_required_transfer_minutes == 45
    assert route.evidence.max_single_transfer_minutes == 25
    assert route.evidence.walking_distance_km == 3.9
    assert route.evidence.encoded_polyline == "leg-1"
    assert route.evidence.encoded_polyline_segments == ["leg-1", "leg-2"]
    assert route.transit_fare is not None
    assert route.transit_fare.amount_original == 410
