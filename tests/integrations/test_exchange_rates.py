from decimal import Decimal

from travel_planner.integrations.exchange_rates import ExchangeRateClient


def test_fx_client_saves_rate_snapshot_timestamp(httpx_mock):
    httpx_mock.add_response(
        url="https://v6.exchangerate-api.com/v6/key/pair/JPY/TWD",
        json={
            "result": "success",
            "conversion_rate": 0.2,
            "time_last_update_utc": "Sat, 23 May 2026 00:00:01 +0000",
        },
    )

    snapshot = ExchangeRateClient("key").snapshot("JPY", "TWD")

    assert snapshot.rate == Decimal("0.2")
    assert snapshot.provider == "ExchangeRate-API"
