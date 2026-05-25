import json
from urllib.parse import quote

try:  # pragma: no cover - exercised only when streamlit is installed
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover - import fallback for test environments
    st = None

from travel_planner.domain.models import PlaceStop


def build_preview_map_src(
    api_key: str,
    *,
    destination_stop: PlaceStop | None,
    hotel_candidates: list[PlaceStop],
    must_visit_stops: list[PlaceStop],
    selected_hotel_place_id: str | None,
    height: int = 420,
) -> str:
    payload = {
        "apiKey": api_key,
        "destinationStop": _serialize_stop(destination_stop),
        "hotelCandidates": [_serialize_stop(stop) for stop in hotel_candidates if stop.place_id],
        "mustVisitStops": [_serialize_stop(stop) for stop in must_visit_stops if stop.place_id],
        "selectedHotelPlaceId": selected_hotel_place_id,
        "height": height - 20,
    }
    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <style>
        html, body, #travel-map {{
          margin: 0;
          padding: 0;
          width: 100%;
          height: 100%;
          overflow: hidden;
          background: #f3f4f6;
        }}
      </style>
    </head>
    <body>
      <div id="travel-map"></div>
      <script>
        const payload = {json.dumps(payload)};
        const loadMarker = (service, map, bounds, entry, options = {{}}) => {{
          if (!entry?.placeId) return;
          service.getDetails({{ placeId: entry.placeId, fields: ["name", "geometry"] }}, (place, status) => {{
            if (status !== google.maps.places.PlacesServiceStatus.OK || !place?.geometry?.location) {{
              return;
            }}
            const markerOptions = {{
              map,
              position: place.geometry.location,
              title: place.name ?? entry.name,
            }};
            if (options.label) markerOptions.label = options.label;
            if (options.color) {{
              markerOptions.icon = {{
                path: google.maps.SymbolPath.CIRCLE,
                scale: 8,
                fillColor: options.color,
                fillOpacity: 1,
                strokeColor: "#111827",
                strokeWeight: 1,
              }};
            }}
            new google.maps.Marker(markerOptions);
            bounds.extend(place.geometry.location);
            map.fitBounds(bounds);
          }});
        }};
        const mount = () => {{
          const map = new google.maps.Map(document.getElementById("travel-map"), {{
            zoom: 12,
            center: {{ lat: 35.4437, lng: 139.6380 }},
            mapTypeControl: false,
            streetViewControl: false,
          }});
          const bounds = new google.maps.LatLngBounds();
          const service = new google.maps.places.PlacesService(map);
          loadMarker(service, map, bounds, payload.destinationStop, {{ color: "#2563eb" }});
          payload.hotelCandidates.forEach((entry, index) => {{
            loadMarker(service, map, bounds, entry, {{
              label: `${{index + 1}}`,
              color: entry.placeId === payload.selectedHotelPlaceId ? "#2563eb" : "#f59e0b",
            }});
          }});
          payload.mustVisitStops.forEach((entry) => {{
            loadMarker(service, map, bounds, entry, {{ color: "#dc2626" }});
          }});
          if (!bounds.isEmpty()) {{
            map.fitBounds(bounds);
          }}
        }};
        window.travelPlannerInitPreviewMap = mount;
      </script>
      <script src="https://maps.googleapis.com/maps/api/js?key={api_key}&libraries=places&callback=travelPlannerInitPreviewMap" async></script>
    </body>
    </html>
    """
    return f"data:text/html;charset=utf-8,{quote(html)}"


def build_verified_route_map_src(
    api_key: str,
    stops: list[PlaceStop],
    encoded_polyline_segments: list[str],
    *,
    height: int = 420,
) -> str:
    verified_place_ids = [stop.place_id for stop in stops if stop.place_id]
    payload = {
        "apiKey": api_key,
        "placeIds": verified_place_ids,
        "encodedPolylineSegments": encoded_polyline_segments,
        "height": height - 20,
    }
    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <style>
        html, body, #travel-map {{
          margin: 0;
          padding: 0;
          width: 100%;
          height: 100%;
          overflow: hidden;
          background: #f3f4f6;
        }}
      </style>
    </head>
    <body>
      <div id="travel-map"></div>
      <script>
        const payload = {json.dumps(payload)};
        const mount = () => {{
          const map = new google.maps.Map(document.getElementById("travel-map"), {{
            zoom: 12,
            center: {{ lat: 34.6937, lng: 135.5023 }},
            mapTypeControl: false,
            streetViewControl: false,
          }});
          const bounds = new google.maps.LatLngBounds();
          payload.encodedPolylineSegments.forEach((encodedPolyline) => {{
            const routePath = google.maps.geometry.encoding.decodePath(encodedPolyline);
            new google.maps.Polyline({{
              path: routePath,
              strokeColor: "#2563eb",
              strokeOpacity: 0.85,
              strokeWeight: 4,
              map,
            }});
            routePath.forEach((point) => bounds.extend(point));
          }});
          const service = new google.maps.places.PlacesService(map);
          payload.placeIds.forEach((placeId, index) => {{
            service.getDetails({{ placeId, fields: ["name", "geometry"] }}, (place, status) => {{
              if (status !== google.maps.places.PlacesServiceStatus.OK || !place?.geometry?.location) {{
                return;
              }}
              new google.maps.Marker({{
                map,
                position: place.geometry.location,
                title: `${{index + 1}}. ${{place.name ?? placeId}}`,
                label: `${{index + 1}}`,
              }});
              bounds.extend(place.geometry.location);
              map.fitBounds(bounds);
            }});
          }});
          if (!bounds.isEmpty()) {{
            map.fitBounds(bounds);
          }}
        }};
        window.travelPlannerInitMap = mount;
      </script>
      <script src="https://maps.googleapis.com/maps/api/js?key={api_key}&libraries=places,geometry&callback=travelPlannerInitMap" async></script>
    </body>
    </html>
    """
    return f"data:text/html;charset=utf-8,{quote(html)}"


def render_verified_route_map(
    api_key: str,
    stops: list[PlaceStop],
    encoded_polyline: str | None,
    encoded_polyline_segments: list[str] | None = None,
    *,
    height: int = 420,
) -> None:
    polyline_segments = encoded_polyline_segments or ([encoded_polyline] if encoded_polyline else [])
    verified_place_ids = [stop.place_id for stop in stops if stop.place_id]
    if st is None or not verified_place_ids or not polyline_segments:
        return

    src = build_verified_route_map_src(
        api_key=api_key,
        stops=stops,
        encoded_polyline_segments=polyline_segments,
        height=height,
    )
    st.iframe(src, height=height, width="stretch")


def render_preview_map(
    api_key: str,
    *,
    destination_stop: PlaceStop | None,
    hotel_candidates: list[PlaceStop],
    must_visit_stops: list[PlaceStop],
    selected_hotel_place_id: str | None,
    height: int = 420,
) -> None:
    has_preview_data = any(
        [
            destination_stop and destination_stop.place_id,
            any(stop.place_id for stop in hotel_candidates),
            any(stop.place_id for stop in must_visit_stops),
        ]
    )
    if st is None or not has_preview_data:
        return

    src = build_preview_map_src(
        api_key=api_key,
        destination_stop=destination_stop,
        hotel_candidates=hotel_candidates,
        must_visit_stops=must_visit_stops,
        selected_hotel_place_id=selected_hotel_place_id,
        height=height,
    )
    st.iframe(src, height=height, width="stretch")


def _serialize_stop(stop: PlaceStop | None) -> dict[str, str] | None:
    if stop is None or not stop.place_id:
        return None
    return {"name": stop.name, "placeId": stop.place_id}
