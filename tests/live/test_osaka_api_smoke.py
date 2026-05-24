import os

import pytest

from travel_planner.integrations.exchange_rates import ExchangeRateClient
from travel_planner.integrations.google_places import GooglePlacesClient
from travel_planner.integrations.google_routes import GoogleRoutesClient


@pytest.mark.live_api
@pytest.mark.skipif(not os.getenv("GOOGLE_MAPS_API_KEY"), reason="requires Google Maps key")
def test_osaka_dotonbori_can_be_grounded_live():
    place = GooglePlacesClient(os.environ["GOOGLE_MAPS_API_KEY"]).ground("Dotonbori Osaka Japan")

    assert place.place_id


@pytest.mark.live_api
@pytest.mark.skipif(not os.getenv("EXCHANGE_RATE_API_KEY"), reason="requires exchange rate key")
def test_jpy_twd_snapshot_can_be_fetched_live():
    snapshot = ExchangeRateClient(os.environ["EXCHANGE_RATE_API_KEY"]).snapshot("JPY", "TWD")

    assert snapshot.rate > 0


@pytest.mark.live_api
@pytest.mark.skipif(not os.getenv("GOOGLE_MAPS_API_KEY"), reason="requires Google Maps key")
def test_osaka_route_can_be_computed_live():
    places = GooglePlacesClient(os.environ["GOOGLE_MAPS_API_KEY"])
    route_client = GoogleRoutesClient(os.environ["GOOGLE_MAPS_API_KEY"])
    hotel = places.ground("Osaka Station Japan")
    poi = places.ground("Dotonbori Osaka Japan")

    route = route_client.compute_daily_route([hotel.place_id, poi.place_id, hotel.place_id])

    assert route.evidence.total_required_transfer_minutes >= 0
