from travel_planner.integrations.google_routes import GoogleRoutesClient


def test_route_without_transit_fare_is_valid_but_has_no_verified_price(httpx_mock):
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "3120s",
                    "distanceMeters": 6200,
                    "polyline": {"encodedPolyline": "route"},
                    "legs": [{"duration": "1380s"}, {"duration": "1740s"}],
                }
            ]
        },
    )

    route = GoogleRoutesClient("maps").compute_daily_route(["hotel", "poi", "hotel"])

    assert route.evidence.total_required_transfer_minutes == 52
    assert route.transit_fare is None
