from pydantic import HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_maps_api_key: SecretStr
    exchange_rate_api_key: SecretStr
    azure_openai_api_key: SecretStr
    azure_openai_endpoint: HttpUrl
    azure_openai_deployment: str
    azure_openai_api_version: str
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: HttpUrl = HttpUrl("https://cloud.langfuse.com")

    @field_validator(
        "google_maps_api_key",
        "exchange_rate_api_key",
        "azure_openai_api_key",
        "azure_openai_deployment",
        "azure_openai_api_version",
    )
    @classmethod
    def required_values_must_not_be_blank(cls, value: SecretStr | str) -> SecretStr | str:
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not text.strip():
            raise ValueError("required setting must not be blank")
        return value

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)
