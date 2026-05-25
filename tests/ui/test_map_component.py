from travel_planner.domain.models import PlaceStop
from travel_planner.ui.map_component import build_verified_route_map_src


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
