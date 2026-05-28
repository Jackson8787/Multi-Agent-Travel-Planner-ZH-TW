from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx

from travel_planner.domain.models import ExchangeRateSnapshot


@dataclass(slots=True)
class ExchangeRateClient:
    api_key: str
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(timeout=30.0)

    def snapshot(self, base_currency: str, target_currency: str) -> ExchangeRateSnapshot:
        response = self.client.get(
            f"https://v6.exchangerate-api.com/v6/{self.api_key}/pair/{base_currency}/{target_currency}"
        )
        response.raise_for_status()
        payload = response.json()
        return ExchangeRateSnapshot(
            provider="ExchangeRate-API",
            base_currency=base_currency,
            target_currency=target_currency,
            rate=Decimal(str(payload["conversion_rate"])),
            retrieved_at=datetime.strptime(
                payload["time_last_update_utc"], "%a, %d %b %Y %H:%M:%S %z"
            ),
        )
