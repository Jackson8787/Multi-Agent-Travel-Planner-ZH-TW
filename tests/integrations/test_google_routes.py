from travel_planner.domain.models import RouteMode
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


def test_transit_empty_result_falls_back_to_driving_estimate(httpx_mock):
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={},
    )
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "979s",
                    "distanceMeters": 5793,
                    "polyline": {"encodedPolyline": "drive-leg"},
                    "legs": [{"duration": "979s"}],
                }
            ]
        },
    )

    route = GoogleRoutesClient("maps").compute_daily_route(["origin", "destination"])

    assert route.evidence.total_required_transfer_minutes == 17
    assert route.evidence.walking_distance_km == 5.79
    assert route.evidence.source_provider == "Google Routes API (drive fallback)"
    assert route.transit_fare is None


def test_drive_mode_skips_transit_and_uses_single_drive_request(httpx_mock):
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={
            "routes": [
                {
                    "duration": "600s",
                    "distanceMeters": 1500,
                    "polyline": {"encodedPolyline": "drive-only"},
                    "legs": [{"duration": "600s"}],
                }
            ]
        },
    )

    route = GoogleRoutesClient("maps").compute_daily_route(
        ["origin", "destination"],
        route_mode=RouteMode.DRIVE,
    )

    assert route.evidence.total_required_transfer_minutes == 10
    assert route.evidence.source_provider == "Google Routes API"
