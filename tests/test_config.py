from travel_planner.config import Settings


def test_settings_do_not_require_observability_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "maps")
    monkeypatch.setenv("EXCHANGE_RATE_API_KEY", "fx")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-demo")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    settings = Settings()

    assert settings.google_maps_api_key.get_secret_value() == "maps"
    assert settings.langfuse_enabled is False
