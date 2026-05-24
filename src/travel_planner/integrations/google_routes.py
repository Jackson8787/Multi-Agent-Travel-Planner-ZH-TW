from dataclasses import dataclass
from decimal import Decimal
from math import ceil

import httpx
from pydantic import BaseModel

from travel_planner.domain.models import PriceRecord, PriceStatus, RouteEvidence


class RouteUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RouteResult(BaseModel):
    evidence: RouteEvidence
    transit_fare: PriceRecord | None = None


def _seconds_to_minutes(value: str) -> int:
    return ceil(int(value.removesuffix("s")) / 60)


@dataclass(slots=True)
class GoogleRoutesClient:
    api_key: str
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(timeout=30.0)

    def compute_daily_route(self, place_ids: list[str]) -> RouteResult:
        response = self.client.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "routes.duration,routes.distanceMeters,"
                    "routes.polyline.encodedPolyline,routes.legs.duration,"
                    "routes.travelAdvisory.transitFare"
                ),
            },
            json={
                "origin": {"placeId": place_ids[0]},
                "destination": {"placeId": place_ids[-1]},
                "intermediates": [{"placeId": place_id} for place_id in place_ids[1:-1]],
                "travelMode": "TRANSIT",
                "computeAlternativeRoutes": False,
                "languageCode": "zh-TW",
            },
        )
        response.raise_for_status()
        payload = response.json()
        routes = payload.get("routes", [])
        if not routes:
            raise RouteUnavailable("No Google Routes result for verified stops")

        first = routes[0]
        total_minutes = _seconds_to_minutes(first["duration"])
        leg_minutes = max(_seconds_to_minutes(leg["duration"]) for leg in first.get("legs", []))
        evidence = RouteEvidence(
            total_required_transfer_minutes=total_minutes,
            max_single_transfer_minutes=leg_minutes,
            walking_distance_km=round(first.get("distanceMeters", 0) / 1000, 2),
            encoded_polyline=first.get("polyline", {}).get("encodedPolyline"),
        )

        fare_payload = first.get("travelAdvisory", {}).get("transitFare")
        if fare_payload is None:
            return RouteResult(evidence=evidence)

        units = Decimal(fare_payload.get("units", "0"))
        nanos = Decimal(fare_payload.get("nanos", 0)) / Decimal("1000000000")
        transit_fare = PriceRecord(
            item_id="verified-transit-fare",
            item_name="Transit Fare",
            category="transport",
            amount_original=units + nanos,
            currency_original=fare_payload.get("currencyCode", "JPY"),
            status=PriceStatus.API_VERIFIED_EXACT,
            source_provider="Google Routes API",
        )
        return RouteResult(evidence=evidence, transit_fare=transit_fare)
