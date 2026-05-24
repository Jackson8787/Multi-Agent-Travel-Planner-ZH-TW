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
        payload = response.json()
        places = payload.get("places", [])
        if not places:
            raise GroundingNotFound(query)

        best = places[0]
        name = best.get("displayName", {}).get("text", query)
        return PlaceStop(name=name, place_id=best["id"])
