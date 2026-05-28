# Travel Planner UI And Hotel Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the Streamlit entry flow into a left-form/right-map layout with synced range inputs, immediate destination and must-visit map preview, immediate hotel candidate lookup, and selected-hotel based trip initialization.

**Architecture:** Keep the existing itinerary verification workflow intact and move the new behavior into a pre-workflow UI state machine. Add small focused helpers for form state, hotel candidate lookup, and preview map payloads instead of pushing more branching into the workflow layer. Extend the UI map component to support preview states before the verified itinerary route is available.

**Tech Stack:** Python, Streamlit, Pydantic domain models, Google Places API, Google Maps JavaScript API iframe embed, pytest, ruff

---

## File Structure

### Files to modify

- `src/travel_planner/ui/app.py`
  - Split the current one-shot form submission into a stateful preflight UI with range controls, destination preview, must-visit preview, hotel candidate selection, and selected-hotel based trip submission.
- `src/travel_planner/ui/map_component.py`
  - Extend from verified-route-only rendering to preview rendering for city, hotel candidates, highlighted selected hotel, and must-visit markers with bounds fitting.
- `src/travel_planner/integrations/google_places.py`
  - Add focused lookup helpers for destination preview and hotel candidate search without disturbing the existing `ground()` contract used by workflow code.
- `tests/ui/test_view_models.py`
  - Cover new validation helpers and user-facing error formatting.
- `tests/ui/test_map_component.py`
  - Cover preview map payload generation and highlight/bounds behavior.
- `tests/integrations/test_google_places.py`
  - Cover new destination lookup and hotel candidate lookup adapter behavior.
- `tests/workflow/test_demo_acceptance.py`
  - Adjust only if selected-hotel based trip initialization changes existing deterministic demo setup paths.

### Files to create

- `src/travel_planner/ui/form_state.py`
  - Hold small dataclasses/helpers for synchronized range inputs, selected hotel state, and must-visit preview state so `app.py` does not absorb all UI state logic.
- `tests/ui/test_form_state.py`
  - Unit tests for range bounds, sync helpers, and candidate selection state.

### Files intentionally not changed

- `src/travel_planner/workflow/orchestrator.py`
  - Existing day-planning and decision-gate logic should remain unchanged.
- `src/travel_planner/agents/runner.py`
  - Agent execution path stays as-is; this feature changes the preflight UI and trip-spec construction only.

---

### Task 1: Add Form-State Helpers For Range Inputs And Selection State

**Files:**
- Create: `src/travel_planner/ui/form_state.py`
- Create: `tests/ui/test_form_state.py`

- [ ] **Step 1: Write the failing tests**

```python
from decimal import Decimal

import pytest

from travel_planner.ui.form_state import (
    RangeFieldSpec,
    coerce_range_value,
    build_range_error,
)


def test_coerce_range_value_accepts_in_bounds_integer():
    spec = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)

    assert coerce_range_value("5", spec) == 5


def test_coerce_range_value_rejects_out_of_bounds_integer():
    spec = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)

    with pytest.raises(ValueError, match="旅遊天數必須介於 1 到 10 天"):
        coerce_range_value("11", spec)


def test_coerce_range_value_accepts_decimal_budget():
    spec = RangeFieldSpec(label="總預算", minimum=1000, maximum=300000, step=1000, unit="NTD")

    assert coerce_range_value("25000", spec) == Decimal("25000")


def test_build_range_error_uses_currency_unit():
    spec = RangeFieldSpec(label="住宿預算", minimum=0, maximum=150000, step=1000, unit="NTD")

    assert build_range_error(spec) == "住宿預算必須介於 0 到 150,000 NTD。"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_form_state.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `travel_planner.ui.form_state`.

- [ ] **Step 3: Write the minimal implementation**

```python
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class RangeFieldSpec:
    label: str
    minimum: int
    maximum: int
    step: int
    unit: str | None = None


def build_range_error(spec: RangeFieldSpec) -> str:
    suffix = f" {spec.unit}" if spec.unit else " 天"
    return f"{spec.label}必須介於 {spec.minimum:,} 到 {spec.maximum:,}{suffix}。"


def coerce_range_value(raw: str | int | float, spec: RangeFieldSpec) -> int | Decimal:
    try:
        numeric = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(build_range_error(spec)) from error
    if numeric < spec.minimum or numeric > spec.maximum:
        raise ValueError(build_range_error(spec))
    if spec.step == 1 and spec.unit is None:
        return int(numeric)
    return numeric
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_form_state.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/travel_planner/ui/form_state.py tests/ui/test_form_state.py
git commit -m "feat: add travel planner form state helpers"
```

### Task 2: Extend Google Places Adapter For Destination And Hotel Candidate Lookup

**Files:**
- Modify: `src/travel_planner/integrations/google_places.py`
- Modify: `tests/integrations/test_google_places.py`

- [ ] **Step 1: Write the failing tests**

```python
from travel_planner.integrations.google_places import GooglePlacesClient


def test_lookup_destination_returns_city_anchor(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={
            "places": [
                {
                    "id": "dest123",
                    "displayName": {"text": "Yokohama"},
                    "location": {"latitude": 35.4437, "longitude": 139.6380},
                }
            ]
        },
    )

    result = GooglePlacesClient("maps").lookup_destination("橫濱")

    assert result.name == "Yokohama"
    assert result.place_id == "dest123"


def test_search_hotel_candidates_returns_three_ranked_places(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={
            "places": [
                {"id": "h1", "displayName": {"text": "Hotel A"}},
                {"id": "h2", "displayName": {"text": "Hotel B"}},
                {"id": "h3", "displayName": {"text": "Hotel C"}},
                {"id": "h4", "displayName": {"text": "Hotel D"}},
            ]
        },
    )

    results = GooglePlacesClient("maps").search_hotel_candidates("橫濱", max_results=3)

    assert [hotel.place_id for hotel in results] == ["h1", "h2", "h3"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/integrations/test_google_places.py -q
```

Expected: FAIL because `lookup_destination` and `search_hotel_candidates` do not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/integrations/test_google_places.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/travel_planner/integrations/google_places.py tests/integrations/test_google_places.py
git commit -m "feat: add destination and hotel candidate places lookups"
```

### Task 3: Add Preview Map Payload Builder For Destination, Hotel Candidates, And Must-Visit Stops

**Files:**
- Modify: `src/travel_planner/ui/map_component.py`
- Modify: `tests/ui/test_map_component.py`

- [ ] **Step 1: Write the failing tests**

```python
from travel_planner.domain.models import PlaceStop
from travel_planner.ui.map_component import build_preview_map_src


def test_preview_map_src_includes_candidate_and_must_visit_payload():
    src = build_preview_map_src(
        api_key="maps",
        destination_label="Yokohama",
        hotel_candidates=[PlaceStop(name="Hotel A", place_id="h1")],
        must_visit_stops=[PlaceStop(name="Cup Noodles Museum", place_id="p1")],
        selected_hotel_place_id="h1",
    )

    assert "Hotel A" in src
    assert "Cup Noodles Museum" in src
    assert "selectedHotelPlaceId" in src
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_map_component.py -q
```

Expected: FAIL because `build_preview_map_src` does not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
def build_preview_map_src(
    api_key: str,
    *,
    destination_label: str | None,
    hotel_candidates: list[PlaceStop],
    must_visit_stops: list[PlaceStop],
    selected_hotel_place_id: str | None,
    height: int = 420,
) -> str:
    payload = {
        "apiKey": api_key,
        "destinationLabel": destination_label,
        "hotelCandidates": [
            {"name": stop.name, "placeId": stop.place_id}
            for stop in hotel_candidates
            if stop.place_id
        ],
        "mustVisitStops": [
            {"name": stop.name, "placeId": stop.place_id}
            for stop in must_visit_stops
            if stop.place_id
        ],
        "selectedHotelPlaceId": selected_hotel_place_id,
        "height": height - 20,
    }
    html = f\"\"\"
    <!doctype html>
    <html>
    <head><meta charset="utf-8" /></head>
    <body>
      <div id="travel-map"></div>
      <script>
        const payload = {json.dumps(payload)};
        const mount = () => {{
          const map = new google.maps.Map(document.getElementById("travel-map"), {{
            zoom: 12,
            center: {{ lat: 35.4437, lng: 139.6380 }},
            mapTypeControl: false,
            streetViewControl: false,
          }});
          const bounds = new google.maps.LatLngBounds();
          const service = new google.maps.places.PlacesService(map);
          const loadMarker = (entry, color) => {{
            service.getDetails({{ placeId: entry.placeId, fields: ["name", "geometry"] }}, (place, status) => {{
              if (status !== google.maps.places.PlacesServiceStatus.OK || !place?.geometry?.location) return;
              new google.maps.Marker({{
                map,
                position: place.geometry.location,
                title: place.name ?? entry.name,
                icon: color ? {{ path: google.maps.SymbolPath.CIRCLE, scale: 8, fillColor: color, fillOpacity: 1, strokeWeight: 1 }} : undefined,
              }});
              bounds.extend(place.geometry.location);
              map.fitBounds(bounds);
            }});
          }};
          payload.hotelCandidates.forEach((entry) => loadMarker(entry, entry.placeId === payload.selectedHotelPlaceId ? "#2563eb" : "#f59e0b"));
          payload.mustVisitStops.forEach((entry) => loadMarker(entry, "#dc2626"));
        }};
        window.travelPlannerInitPreviewMap = mount;
      </script>
      <script src="https://maps.googleapis.com/maps/api/js?key={api_key}&libraries=places&callback=travelPlannerInitPreviewMap" async></script>
    </body>
    </html>
    \"\"\"
    return f"data:text/html;charset=utf-8,{quote(html)}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_map_component.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/travel_planner/ui/map_component.py tests/ui/test_map_component.py
git commit -m "feat: add travel planner preview map payloads"
```

### Task 4: Add App-Level Validation And Must-Visit Preview Formatting

**Files:**
- Modify: `src/travel_planner/ui/app.py`
- Modify: `tests/ui/test_view_models.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from travel_planner.ui.app import (
    build_must_visit_preview_queries,
    validate_trip_submission_inputs,
)


def test_build_must_visit_preview_queries_splits_lines_and_commas():
    queries = build_must_visit_preview_queries("鋼彈工廠, 紅磚倉庫\n杯麵博物館")

    assert queries == ["鋼彈工廠", "紅磚倉庫", "杯麵博物館"]


def test_validate_trip_submission_inputs_requires_selected_hotel():
    with pytest.raises(ValueError, match="請先從住宿候補中選擇一間住宿"):
        validate_trip_submission_inputs(
            destination="橫濱",
            days=2,
            budget_amount="25000",
            lodging_budget_amount="8000",
            selected_hotel_place_id=None,
            must_visit_name="",
            must_visit_price="",
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_view_models.py -q
```

Expected: FAIL because the helpers do not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
def build_must_visit_preview_queries(raw_text: str) -> list[str]:
    separators_normalized = raw_text.replace("\n", ",")
    return [part.strip() for part in separators_normalized.split(",") if part.strip()]


def validate_trip_submission_inputs(
    *,
    destination: str,
    days: int,
    budget_amount: str,
    lodging_budget_amount: str,
    selected_hotel_place_id: str | None,
    must_visit_name: str,
    must_visit_price: str,
) -> None:
    if not destination.strip():
        raise ValueError("目的地不能空白")
    if not selected_hotel_place_id:
        raise ValueError("請先從住宿候補中選擇一間住宿。")
    if must_visit_price.strip() and not must_visit_name.strip():
        raise ValueError("填寫必去景點價格前，請先輸入必去景點名稱")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_view_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/travel_planner/ui/app.py tests/ui/test_view_models.py
git commit -m "feat: validate travel planner preflight submission"
```

### Task 5: Build The Left-Form/Right-Map Preflight UI

**Files:**
- Modify: `src/travel_planner/ui/app.py`
- Modify: `src/travel_planner/ui/form_state.py`
- Test: `tests/ui/test_form_state.py`
- Test: `tests/ui/test_view_models.py`

- [ ] **Step 1: Write the failing tests**

```python
from travel_planner.ui.form_state import RangeFieldSpec


def test_budget_specs_match_product_bounds():
    from travel_planner.ui.app import DAYS_SPEC, TOTAL_BUDGET_SPEC, LODGING_BUDGET_SPEC

    assert DAYS_SPEC == RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)
    assert TOTAL_BUDGET_SPEC.minimum == 1000
    assert LODGING_BUDGET_SPEC.maximum == 150000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_form_state.py tests/ui/test_view_models.py -q
```

Expected: FAIL because the constants and preflight layout helpers do not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
DAYS_SPEC = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)
TOTAL_BUDGET_SPEC = RangeFieldSpec(label="總預算", minimum=1000, maximum=300000, step=1000, unit="NTD")
LODGING_BUDGET_SPEC = RangeFieldSpec(label="住宿預算", minimum=0, maximum=150000, step=1000, unit="NTD")


def render_trip_spec_form() -> None:
    _require_streamlit()
    left, right = st.columns([1, 1.2])
    with left:
        destination = st.text_input("目的地", value="Osaka")
        days_slider = st.slider("旅遊天數", min_value=1, max_value=10, value=5)
        days_input = st.number_input("旅遊天數（輸入）", min_value=1, max_value=10, value=days_slider)
        total_budget_slider = st.slider("總預算 (NTD)", min_value=1000, max_value=300000, value=25000, step=1000)
        total_budget_input = st.number_input("總預算（輸入）", min_value=1000, max_value=300000, value=total_budget_slider, step=1000)
        lodging_budget_slider = st.slider("住宿預算 (NTD)", min_value=0, max_value=150000, value=8000, step=1000)
        lodging_budget_input = st.number_input("住宿預算（輸入）", min_value=0, max_value=150000, value=lodging_budget_slider, step=1000)
        must_visit_name = st.text_area("必去景點", value="")
        selected_hotel = _render_hotel_candidates(destination=destination, lodging_budget_amount=lodging_budget_input)
        submitted = st.button("開始規劃", disabled=selected_hotel is None)
    with right:
        _render_preflight_map(
            destination=destination,
            hotel_candidates=st.session_state.get("hotel_candidates", []),
            selected_hotel_place_id=selected_hotel.place_id if selected_hotel else None,
            must_visit_queries=build_must_visit_preview_queries(must_visit_name),
        )
```

- [ ] **Step 4: Run the targeted tests**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_form_state.py tests/ui/test_view_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/travel_planner/ui/app.py src/travel_planner/ui/form_state.py tests/ui/test_form_state.py tests/ui/test_view_models.py
git commit -m "feat: add travel planner preflight form layout"
```

### Task 6: Connect Destination Preview, Hotel Candidates, And Must-Visit Preview To Session State

**Files:**
- Modify: `src/travel_planner/ui/app.py`
- Modify: `src/travel_planner/integrations/google_places.py`
- Test: `tests/integrations/test_google_places.py`
- Test: `tests/ui/test_view_models.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_trip_spec_uses_selected_hotel_and_pre_grounded_must_visit():
    from travel_planner.domain.models import PlaceStop
    from travel_planner.ui.app import build_trip_spec_from_preflight

    trip_spec = build_trip_spec_from_preflight(
        settings=Settings(),
        destination="橫濱",
        days=2,
        budget_amount="25000",
        budget_currency="TWD",
        interests="anime, food",
        pace_level=PaceLevel.RELAXED,
        selected_hotel=PlaceStop(name="Hotel A", place_id="h1"),
        grounded_must_visit=[PlaceStop(name="Cup Noodles Museum", place_id="p1")],
        must_visit_name="Cup Noodles Museum",
        must_visit_price="0",
        must_visit_price_url="",
    )

    assert trip_spec.hotel.place_id == "h1"
    assert [stop.place_id for stop in trip_spec.must_visit] == ["p1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_view_models.py -q
```

Expected: FAIL because `build_trip_spec_from_preflight` does not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
def build_trip_spec_from_preflight(
    *,
    settings: Settings,
    destination: str,
    days: int,
    budget_amount: str,
    budget_currency: str,
    interests: str,
    pace_level: PaceLevel,
    selected_hotel: PlaceStop,
    grounded_must_visit: list[PlaceStop],
    must_visit_name: str,
    must_visit_price: str,
    must_visit_price_url: str,
) -> TripSpec:
    return TripSpec(
        destination=destination.strip(),
        days=days,
        budget_amount=Decimal(budget_amount),
        budget_currency=budget_currency,
        interests=[part.strip() for part in interests.split(",") if part.strip()],
        pace=get_pace_profile(pace_level),
        hotel=selected_hotel,
        must_visit=grounded_must_visit,
        prices=[],
        fx_snapshot=ExchangeRateSnapshot.model_validate(
            ExchangeRateClient(settings.exchange_rate_api_key.get_secret_value())
            .snapshot(base_currency="JPY", target_currency=budget_currency)
            .model_dump()
        ),
    )
```

- [ ] **Step 4: Run the targeted tests**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/ui/test_view_models.py tests/integrations/test_google_places.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/travel_planner/ui/app.py src/travel_planner/integrations/google_places.py tests/ui/test_view_models.py tests/integrations/test_google_places.py
git commit -m "feat: initialize trip spec from preflight selections"
```

### Task 7: Verify Preview Map And Workflow Integration End-To-End

**Files:**
- Modify: `tests/workflow/test_demo_acceptance.py`
- Modify: `tests/ui/test_map_component.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing integration assertions**

```python
def test_demo_acceptance_keeps_selected_hotel_before_workflow():
    trip_spec = build_trip_spec_from_preflight(
        settings=settings,
        destination="Osaka",
        days=3,
        budget_amount="25000",
        budget_currency="TWD",
        interests="anime, food",
        pace_level=PaceLevel.RELAXED,
        selected_hotel=PlaceStop(name="Hotel Monterey", place_id="hotel-1"),
        grounded_must_visit=[PlaceStop(name="Dotonbori", place_id="poi-1")],
        must_visit_name="Dotonbori",
        must_visit_price="0",
        must_visit_price_url="",
    )

    assert trip_spec.hotel.place_id == "hotel-1"
    assert trip_spec.must_visit[0].place_id == "poi-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest tests/workflow/test_demo_acceptance.py tests/ui/test_map_component.py -q
```

Expected: FAIL because the deterministic harness does not yet exercise the new preflight state.

- [ ] **Step 3: Update docs and minimal integration code**

```markdown
## Updated UI Flow

1. Enter destination.
2. Adjust total budget and lodging budget with synced slider/input controls.
3. Review destination + must-visit preview on the right map.
4. Select one hotel candidate.
5. Start itinerary planning.
```

- [ ] **Step 4: Run full verification**

Run:

```bash
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest -q
.worktrees/feature-travel-planner-mvp/.venv/bin/python -m ruff check src tests README.md
```

Expected: all tests PASS, ruff PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/workflow/test_demo_acceptance.py tests/ui/test_map_component.py README.md
git commit -m "test: verify travel planner preflight UI workflow"
```

## Self-Review

- Spec coverage:
  - Left-form/right-map layout: Task 5
  - Slider + manual input with bounds: Tasks 1 and 5
  - Destination preview: Tasks 2, 3, 5, 6
  - Hotel candidates and selected-hotel flow: Tasks 2, 3, 5, 6
  - Must-visit preview and fit bounds: Tasks 3, 4, 6, 7
  - Selected hotel as trip entrypoint: Task 6
  - Regression verification: Task 7
- Placeholder scan:
  - No `TODO`, `TBD`, or “implement later” placeholders remain in task steps.
- Type consistency:
  - Plan uses `PlaceStop` for destination-anchored markers, hotel candidates, and must-visit preview entries.
  - Selected hotel is always carried as `selected_hotel: PlaceStop`.
  - Range-bound form fields are defined through `RangeFieldSpec`.
