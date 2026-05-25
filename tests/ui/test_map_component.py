from urllib.parse import unquote

from travel_planner.domain.models import PlaceStop
from travel_planner.ui.map_component import build_preview_map_src, build_verified_route_map_src


def test_build_verified_route_map_src_uses_verified_stops_and_polyline_segments():
    src = build_verified_route_map_src(
        api_key="maps-key",
        stops=[
            PlaceStop(name="Hotel", place_id="hotel-id"),
            PlaceStop(name="Unknown"),
            PlaceStop(name="Dotonbori", place_id="dotonbori-id"),
        ],
        encoded_polyline_segments=["seg-1", "seg-2"],
        height=420,
    )

    assert src.startswith("data:text/html;charset=utf-8,")
    assert "hotel-id" in src
    assert "dotonbori-id" in src
    assert "seg-1" in src
    assert "seg-2" in src
    assert "Unknown" not in src


def test_build_preview_map_src_includes_candidate_and_must_visit_payload():
    src = build_preview_map_src(
        api_key="maps-key",
        destination_stop=PlaceStop(name="Yokohama", place_id="destination-id"),
        hotel_candidates=[PlaceStop(name="Hotel A", place_id="hotel-a-id")],
        must_visit_stops=[PlaceStop(name="Cup Noodles Museum", place_id="museum-id")],
        selected_hotel_place_id="hotel-a-id",
    )
    decoded_src = unquote(src)

    assert src.startswith("data:text/html;charset=utf-8,")
    assert "destination-id" in decoded_src
    assert "hotel-a-id" in decoded_src
    assert "museum-id" in decoded_src
    assert "selectedHotelPlaceId" in decoded_src
    assert "Cup Noodles Museum" in decoded_src
