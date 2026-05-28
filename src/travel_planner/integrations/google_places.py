from dataclasses import dataclass

import httpx

from travel_planner.domain.models import PlaceStop


class GroundingNotFound(Exception):
    def __init__(self, query: str):
        super().__init__(f"No verified place found for {query}")
        self.query = query


@dataclass(slots=True)
class GooglePlacesClient:
    api_key: str
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(timeout=30.0)

    def ground(self, query: str) -> PlaceStop:
        payload = self._search_text(query)
        places = payload.get("places", [])
        if not places:
            raise GroundingNotFound(query)

        best = places[0]
        name = best.get("displayName", {}).get("text", query)
        return PlaceStop(name=name, place_id=best["id"])

    def lookup_destination(self, query: str) -> PlaceStop:
        payload = self._search_text(query)
        places = payload.get("places", [])
        if not places:
            raise GroundingNotFound(query)

        best = places[0]
        return PlaceStop(
            name=best.get("displayName", {}).get("text", query),
            place_id=best["id"],
        )

    def search_hotel_candidates(self, destination: str, *, max_results: int = 3) -> list[PlaceStop]:
        payload = self._search_text(f"hotels in {destination}")
        places = payload.get("places", [])
        if not places:
            raise GroundingNotFound(destination)

        return [
            PlaceStop(
                name=place.get("displayName", {}).get("text", destination),
                place_id=place["id"],
            )
            for place in places[:max_results]
        ]

    def _search_text(self, query: str) -> dict:
        response = self.client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.location,"
                    "places.priceLevel,places.priceRange"
                ),
            },
            json={"textQuery": query, "languageCode": "zh-TW"},
        )
        response.raise_for_status()
        return response.json()
