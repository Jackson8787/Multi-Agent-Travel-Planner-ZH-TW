from enum import StrEnum

from pydantic import BaseModel, HttpUrl


class ProviderKey(StrEnum):
    GOOGLE_PLACES_NEW = "GOOGLE_PLACES_NEW"
    GOOGLE_ROUTES = "GOOGLE_ROUTES"
    EXCHANGE_RATE_API = "EXCHANGE_RATE_API"
    AZURE_OPENAI = "AZURE_OPENAI"
    LANGFUSE = "LANGFUSE"


class ProviderMetadata(BaseModel):
    key: ProviderKey
    display_name: str
    purpose: str
    docs_url: HttpUrl
    evidence_fields: list[str]
    limitations: str


PROVIDERS = {
    ProviderKey.GOOGLE_PLACES_NEW: ProviderMetadata(
        key=ProviderKey.GOOGLE_PLACES_NEW,
        display_name="Google Places API (New)",
        purpose="Ground places and restaurants to identifiers and coordinates.",
        docs_url="https://developers.google.com/maps/documentation/places/web-service/text-search",
        evidence_fields=["place_id", "location", "priceLevel", "priceRange"],
        limitations="Restaurant price fields may be absent or ranges, not checkout totals.",
    ),
    ProviderKey.GOOGLE_ROUTES: ProviderMetadata(
        key=ProviderKey.GOOGLE_ROUTES,
        display_name="Google Routes API",
        purpose="Validate travel duration, route shape, walking distance and available fare.",
        docs_url="https://developers.google.com/maps/documentation/routes",
        evidence_fields=["duration", "distanceMeters", "polyline", "transitFare"],
        limitations="Transit fare is only available on supported all-transit route results.",
    ),
    ProviderKey.EXCHANGE_RATE_API: ProviderMetadata(
        key=ProviderKey.EXCHANGE_RATE_API,
        display_name="ExchangeRate-API",
        purpose="Create a JPY/TWD conversion snapshot for one trip.",
        docs_url="https://www.exchangerate-api.com/docs/overview",
        evidence_fields=["conversion_rate", "time_last_update_utc"],
        limitations="Converted amounts are estimated budgets, not card settlement totals.",
    ),
    ProviderKey.AZURE_OPENAI: ProviderMetadata(
        key=ProviderKey.AZURE_OPENAI,
        display_name="Azure OpenAI",
        purpose="Generate agent proposals and review explanations.",
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
        evidence_fields=["model_deployment", "agent_output"],
        limitations="Generated recommendations require separate tool verification.",
    ),
    ProviderKey.LANGFUSE: ProviderMetadata(
        key=ProviderKey.LANGFUSE,
        display_name="Langfuse",
        purpose="Record traces, decisions and evaluation observations.",
        docs_url="https://langfuse.com/docs/observability/overview",
        evidence_fields=["trace_id", "observation_type"],
        limitations="Telemetry failure must not turn unverified plans into approved plans.",
    ),
}


def get_provider(key: ProviderKey) -> ProviderMetadata:
    return PROVIDERS[key]
