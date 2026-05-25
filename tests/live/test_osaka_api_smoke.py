import os

import pytest
from langfuse import Langfuse
from openai import OpenAI

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


@pytest.mark.live_api
@pytest.mark.skipif(
    not (
        os.getenv("AZURE_OPENAI_API_KEY")
        and os.getenv("AZURE_OPENAI_ENDPOINT")
        and os.getenv("AZURE_OPENAI_DEPLOYMENT")
    ),
    reason="requires Azure OpenAI credentials",
)
def test_azure_openai_gpt5_mini_responses_api_can_reply_live():
    client = OpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )

    response = client.responses.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        input="Reply with the exact text OK.",
        reasoning={"effort": "low"},
        max_output_tokens=120,
    )

    assert response.output_text.strip() == "OK"


@pytest.mark.live_api
@pytest.mark.skipif(
    not (
        os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_SECRET_KEY")
        and (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL"))
    ),
    reason="requires Langfuse credentials and host",
)
def test_langfuse_can_authenticate_live():
    client = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.getenv("LANGFUSE_HOST") or os.environ["LANGFUSE_BASE_URL"],
    )

    assert client.auth_check() is True
