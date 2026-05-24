import pytest
from pydantic import ValidationError

from travel_planner.config import Settings


def test_settings_do_not_require_observability_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "maps")
    monkeypatch.setenv("EXCHANGE_RATE_API_KEY", "fx")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-demo")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    settings = Settings(_env_file=None)

    assert settings.google_maps_api_key.get_secret_value() == "maps"
    assert settings.langfuse_enabled is False


def test_settings_do_not_enable_observability_for_blank_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "maps")
    monkeypatch.setenv("EXCHANGE_RATE_API_KEY", "fx")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-demo")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", " ")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "\t")

    settings = Settings(_env_file=None)

    assert settings.langfuse_enabled is False


@pytest.mark.parametrize(
    "field_name",
    [
        "google_maps_api_key",
        "exchange_rate_api_key",
        "azure_openai_api_key",
        "azure_openai_deployment",
        "azure_openai_api_version",
    ],
)
@pytest.mark.parametrize("blank_value", ["", " \t"])
def test_settings_reject_blank_required_values(field_name, blank_value):
    values = {
        "google_maps_api_key": "maps",
        "exchange_rate_api_key": "fx",
        "azure_openai_api_key": "azure",
        "azure_openai_endpoint": "https://example.openai.azure.com/",
        "azure_openai_deployment": "gpt-demo",
        "azure_openai_api_version": "2024-10-21",
    }
    values[field_name] = blank_value

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_settings_reject_invalid_langfuse_host():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            google_maps_api_key="maps",
            exchange_rate_api_key="fx",
            azure_openai_api_key="azure",
            azure_openai_endpoint="https://example.openai.azure.com/",
            azure_openai_deployment="gpt-demo",
            azure_openai_api_version="2024-10-21",
            langfuse_host="not a url",
        )
