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
        if len(place_ids) < 2:
            raise RouteUnavailable("At least two verified stops are required")

        total_minutes = 0
        max_single_minutes = 0
        total_distance_meters = 0
        polyline_segments: list[str] = []
        fare_total = Decimal("0")
        fare_currency: str | None = None

        for origin, destination in zip(place_ids[:-1], place_ids[1:], strict=True):
            first = self._compute_leg(origin, destination)
            total_minutes += _seconds_to_minutes(first["duration"])
            leg_minutes = max(_seconds_to_minutes(leg["duration"]) for leg in first.get("legs", []))
            max_single_minutes = max(max_single_minutes, leg_minutes)
            total_distance_meters += first.get("distanceMeters", 0)
            polyline = first.get("polyline", {}).get("encodedPolyline")
            if polyline:
                polyline_segments.append(polyline)

            fare_payload = first.get("travelAdvisory", {}).get("transitFare")
            if fare_payload is not None:
                units = Decimal(fare_payload.get("units", "0"))
                nanos = Decimal(fare_payload.get("nanos", 0)) / Decimal("1000000000")
                fare_total += units + nanos
                fare_currency = fare_payload.get("currencyCode", fare_currency or "JPY")

        evidence = RouteEvidence(
            total_required_transfer_minutes=total_minutes,
            max_single_transfer_minutes=max_single_minutes,
            walking_distance_km=round(total_distance_meters / 1000, 2),
            encoded_polyline=polyline_segments[0] if polyline_segments else None,
            encoded_polyline_segments=polyline_segments,
        )
        if fare_currency is None:
            return RouteResult(evidence=evidence)
        transit_fare = PriceRecord(
            item_id="verified-transit-fare",
            item_name="Transit Fare",
            category="transport",
            amount_original=fare_total,
            currency_original=fare_currency,
            status=PriceStatus.API_VERIFIED_EXACT,
            source_provider="Google Routes API",
        )
        return RouteResult(evidence=evidence, transit_fare=transit_fare)

    def _compute_leg(self, origin_place_id: str, destination_place_id: str) -> dict:
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
                "origin": {"placeId": origin_place_id},
                "destination": {"placeId": destination_place_id},
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
        return routes[0]
