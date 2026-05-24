from travel_planner.integrations.api_registry import ProviderKey, get_provider


def test_registry_discloses_price_and_route_source_limitations():
    routes = get_provider(ProviderKey.GOOGLE_ROUTES)
    fx = get_provider(ProviderKey.EXCHANGE_RATE_API)

    assert "transit fare" in routes.limitations.lower()
    assert "estimated" in fx.limitations.lower()
    assert str(routes.docs_url).startswith("https://developers.google.com/")


def test_registry_never_stores_credentials():
    provider = get_provider(ProviderKey.GOOGLE_PLACES_NEW)

    serialized = provider.model_dump_json().lower()
    assert "api_key" not in serialized
    assert "secret" not in serialized
