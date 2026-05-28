# Multi-Agent Travel Planner

Superpowers-guided Osaka travel planning demo with typed trip state, a left-form/right-map preflight UI, verification gates, and human decisions for pace and budget conflicts.

## Setup

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env`.
3. Fill in Google Maps, ExchangeRate-API, Azure OpenAI, and optional Langfuse credentials.
4. Start the app:

```bash
python -m streamlit run src/travel_planner/ui/app.py
```

## Required Environment Variables

```dotenv
GOOGLE_MAPS_API_KEY=
EXCHANGE_RATE_API_KEY=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=
AZURE_OPENAI_API_VERSION=2024-10-21
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

## Data Provenance

- Places and restaurant grounding: Google Places API (New).
- Route duration, encoded polyline, walking distance, and available transit fare: Google Routes API.
- Currency conversion snapshot: ExchangeRate-API. Converted TWD amounts are budget estimates, not card settlement totals.
- Admission and lodging exact costs: user-confirmed official URLs.
- Agent output: Azure OpenAI suggestions. These are never treated as verified facts without tool verification.

The centralized provider registry for display metadata and maintenance lives at:

- `src/travel_planner/integrations/api_registry.py`

## Demo Workflow

1. Enter destination, travel days, total budget, and lodging budget through synced slider/input controls.
2. Review the right-side preview map as destination, hotel candidates, and must-visit places resolve.
3. Select one hotel candidate from the left panel.
4. Start itinerary planning with the selected hotel and any grounded must-visit places.
5. Generate itinerary candidates through the itinerary agent and verify places plus routes through Google APIs.
6. Pause for user decisions when pace or budget conflicts occur.
7. Approve only verified day plans and display evidence plus the verified route map.

## Verification Commands

Run the deterministic suite:

```bash
python -m pytest -q
```

Run live smoke checks with configured credentials:

```bash
python -m pytest -m live_api tests/live -q
```

Run Ruff:

```bash
ruff check src tests
```

## Notes

- The app uses verified place IDs and encoded polylines when rendering the route map; it does not draw maps from unverified place names.
- If Streamlit is not installed in the current Python environment, the formatter tests still import successfully, but the UI itself will not render until Streamlit is installed.
