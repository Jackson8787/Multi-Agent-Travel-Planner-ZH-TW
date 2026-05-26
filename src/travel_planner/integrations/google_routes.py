from dataclasses import dataclass
from decimal import Decimal
from math import ceil

import httpx
from pydantic import BaseModel

from travel_planner.domain.models import PriceRecord, PriceStatus, RouteEvidence, RouteMode, RouteLeg


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

    def compute_daily_route(
        self,
        place_ids: list[str],
        *,
        route_mode: RouteMode = RouteMode.AUTO,
    ) -> RouteResult:
        if len(place_ids) < 2:
            raise RouteUnavailable("At least two verified stops are required")

        total_minutes = 0
        max_single_minutes = 0
        total_walk_meters = 0   # WALK-mode steps only; transit/drive distances excluded
        total_distance_meters = 0  # all transport modes combined
        polyline_segments: list[str] = []
        fare_total = Decimal("0")
        fare_currency: str | None = None
        used_drive_fallback = False
        route_legs: list[RouteLeg] = []

        for origin, destination in zip(place_ids[:-1], place_ids[1:], strict=True):
            first, used_leg_drive_fallback = self._compute_leg(
                origin,
                destination,
                route_mode=route_mode,
            )
            used_drive_fallback = used_drive_fallback or used_leg_drive_fallback
            leg_duration = _seconds_to_minutes(first["duration"])
            total_minutes += leg_duration
            raw_leg_minutes = max(
                (_seconds_to_minutes(leg["duration"]) for leg in first.get("legs", [])),
                default=leg_duration,
            )
            max_single_minutes = max(max_single_minutes, raw_leg_minutes)

            # Accumulate total route distance (all modes).
            total_distance_meters += first.get("distanceMeters", 0)

            # Sum only WALK-mode step distances to get accurate walking distance.
            # The route-level distanceMeters includes transit (train/bus) distance,
            # which is meaningless as a "walking" metric.
            leg_walk_meters = sum(
                step.get("distanceMeters", 0)
                for api_leg in first.get("legs", [])
                for step in api_leg.get("steps", [])
                if step.get("travelMode") == "WALK"
            )
            total_walk_meters += leg_walk_meters

            polyline = first.get("polyline", {}).get("encodedPolyline")
            if polyline:
                polyline_segments.append(polyline)

            fare_payload = first.get("travelAdvisory", {}).get("transitFare")
            if fare_payload is not None:
                units = Decimal(fare_payload.get("units", "0"))
                nanos = Decimal(fare_payload.get("nanos", 0)) / Decimal("1000000000")
                fare_total += units + nanos
                fare_currency = fare_payload.get("currencyCode", fare_currency or "JPY")

            route_legs.append(
                RouteLeg(
                    duration_minutes=leg_duration,
                    travel_mode="DRIVE" if used_leg_drive_fallback else "TRANSIT",
                    walk_distance_km=round(leg_walk_meters / 1000, 3),
                )
            )

        evidence = RouteEvidence(
            total_required_transfer_minutes=total_minutes,
            max_single_transfer_minutes=max_single_minutes,
            walking_distance_km=round(total_walk_meters / 1000, 2),
            total_distance_km=round(total_distance_meters / 1000, 2),
            encoded_polyline=polyline_segments[0] if polyline_segments else None,
            encoded_polyline_segments=polyline_segments,
            source_provider=(
                "Google Routes API (drive fallback)"
                if used_drive_fallback
                else "Google Routes API"
            ),
            legs=route_legs,
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

    def _compute_leg(
        self,
        origin_place_id: str,
        destination_place_id: str,
        *,
        route_mode: RouteMode,
    ) -> tuple[dict, bool]:
        if route_mode is RouteMode.DRIVE:
            drive_payload = self._request_leg(
                origin_place_id=origin_place_id,
                destination_place_id=destination_place_id,
                travel_mode="DRIVE",
            )
            drive_routes = drive_payload.get("routes", [])
            if drive_routes:
                return drive_routes[0], False
            raise RouteUnavailable("No Google Routes result for verified stops")

        transit_payload = self._request_leg(
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            travel_mode="TRANSIT",
        )
        transit_routes = transit_payload.get("routes", [])
        if transit_routes:
            return transit_routes[0], False

        if route_mode is RouteMode.TRANSIT:
            raise RouteUnavailable("No Google Routes result for verified stops")

        drive_payload = self._request_leg(
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            travel_mode="DRIVE",
        )
        drive_routes = drive_payload.get("routes", [])
        if drive_routes:
            return drive_routes[0], True
        raise RouteUnavailable("No Google Routes result for verified stops")

    def _request_leg(self, *, origin_place_id: str, destination_place_id: str, travel_mode: str) -> dict:
        response = self.client.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "routes.duration,routes.distanceMeters,"
                    "routes.polyline.encodedPolyline,"
                    "routes.legs.duration,"
                    "routes.legs.steps.distanceMeters,"
                    "routes.legs.steps.travelMode,"
                    "routes.travelAdvisory.transitFare"
                ),
            },
            json={
                "origin": {"placeId": origin_place_id},
                "destination": {"placeId": destination_place_id},
                "travelMode": travel_mode,
                "computeAlternativeRoutes": False,
                "languageCode": "zh-TW",
            },
        )
        response.raise_for_status()
        return response.json()
