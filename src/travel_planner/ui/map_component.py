import json

try:  # pragma: no cover - exercised only when streamlit is installed
    import streamlit.components.v1 as components
except ModuleNotFoundError:  # pragma: no cover - import fallback for test environments
    components = None

from travel_planner.domain.models import PlaceStop


def render_verified_route_map(
    api_key: str,
    stops: list[PlaceStop],
    encoded_polyline: str | None,
    *,
    height: int = 420,
) -> None:
    verified_place_ids = [stop.place_id for stop in stops if stop.place_id]
    if components is None or not verified_place_ids or not encoded_polyline:
        return

    payload = json.dumps(
        {
            "apiKey": api_key,
            "placeIds": verified_place_ids,
            "encodedPolyline": encoded_polyline,
        }
    )

    html = f"""
    <div id="travel-map" style="width:100%;height:{height - 20}px;border-radius:8px;"></div>
    <script>
      const payload = {payload};
      const mount = () => {{
        const map = new google.maps.Map(document.getElementById("travel-map"), {{
          zoom: 12,
          center: {{ lat: 34.6937, lng: 135.5023 }},
          mapTypeControl: false,
          streetViewControl: false,
        }});
        const bounds = new google.maps.LatLngBounds();
        const routePath = google.maps.geometry.encoding.decodePath(payload.encodedPolyline);
        new google.maps.Polyline({{
          path: routePath,
          strokeColor: "#2563eb",
          strokeOpacity: 0.85,
          strokeWeight: 4,
          map,
        }});
        routePath.forEach((point) => bounds.extend(point));
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
      if (!window.travelPlannerMapsLoaded) {{
        window.travelPlannerMapsLoaded = true;
        window.travelPlannerInitMap = mount;
        const script = document.createElement("script");
        script.src = `https://maps.googleapis.com/maps/api/js?key=${{payload.apiKey}}&libraries=places,geometry&callback=travelPlannerInitMap`;
        script.async = true;
        document.head.appendChild(script);
      }} else if (window.google?.maps) {{
        mount();
      }}
    </script>
    """
    components.html(html, height=height)
