# Multi-Agent Travel Planner MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Streamlit Osaka travel-planning demo whose five Agent roles operate through a Superpowers-guided, verifiable daily workflow with real Maps and exchange-rate APIs, traceable price evidence, and user decisions for pace and budget conflicts.

**Architecture:** A Python package owns typed trip state and a deterministic orchestrator. CrewAI/Azure OpenAI agents only propose itineraries, meals, and final explanations; adapters own external API calls; validators and calculators own gating decisions. Streamlit persists the workflow state, pauses for user decisions, and exposes evidence and map output.

**Tech Stack:** Python 3.12, Streamlit, Pydantic v2, `httpx`, CrewAI with Azure OpenAI, Google Places API (New), Google Routes API, ExchangeRate-API, Langfuse Python SDK, pytest, Ruff.

---

## Scope And Delivery Order

This is one cohesive MVP rather than independent sub-projects: route verification, budget verification, user decisions, and the UI all depend on one `TripSpec` and `DayPlanState` contract. Build from domain contracts outward so real external calls and LLM calls remain replaceable in tests.

The initial implementation requires these runtime secrets, supplied locally and never committed:

```dotenv
GOOGLE_MAPS_API_KEY=
EXCHANGE_RATE_API_KEY=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=
AZURE_OPENAI_API_VERSION=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

## Planned File Structure

```text
pyproject.toml                         Project metadata, dependencies, pytest/Ruff config
.env.example                           Required environment variable names without secrets
.gitignore                             Local/cache/secret exclusions
README.md                              Setup, credentials, API provenance, demo steps
src/travel_planner/
  __init__.py
  config.py                            Environment settings only
  domain/
    models.py                          TripSpec, price, day, conflict, decision models
    pace.py                            Four pace presets and load rules
  integrations/
    api_registry.py                    Dedicated provider/provenance metadata registry
    google_places.py                   Place and restaurant grounding adapter
    google_routes.py                   Route/time/fare/polyline adapter
    exchange_rates.py                  JPY/TWD snapshot adapter
  validation/
    pace.py                            Deterministic pace gate
    budget.py                          Conversion, totals, price-status budget gate
  agents/
    runner.py                          CrewAI/Azure structured agent calls
    skills/
      itinerary_agent.md               Itinerary constraints and output contract
      food_agent.md                    Restaurant proposal constraints
      reviewer_agent.md                Explanation/evaluation constraints
  observability/
    tracing.py                         Langfuse adapter with no-op fallback
  workflow/
    orchestrator.py                    Daily state machine and human-decision resume flow
  ui/
    app.py                             Streamlit screens and session state
    map_component.py                   Maps JavaScript route renderer
tests/
  domain/test_pace.py
  integrations/test_api_registry.py
  integrations/test_google_places.py
  integrations/test_google_routes.py
  integrations/test_exchange_rates.py
  validation/test_budget.py
  validation/test_pace.py
  agents/test_runner.py
  workflow/test_orchestrator.py
  observability/test_tracing.py
  ui/test_view_models.py
```

## Task 1: Bootstrap The Python Application

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `src/travel_planner/__init__.py`
- Create: `src/travel_planner/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Initialize version control before source implementation**

Run:

```bash
git init
git branch -M main
```

Expected: a new repository exists so later plan checkpoints can be committed. Existing PDF and design documents remain unmodified.

- [ ] **Step 2: Create the packaging and tool configuration**

Write `pyproject.toml` with the concrete runtime and test dependencies:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "multi-agent-travel-planner"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "crewai[openai]>=0.130",
  "httpx>=0.27",
  "langfuse>=3.0",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "streamlit>=1.37",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-mock>=3.14", "ruff>=0.6"]

[tool.hatch.build.targets.wheel]
packages = ["src/travel_planner"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
markers = ["live_api: requires real external API credentials"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

Write `.gitignore` and `.env.example`:

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.streamlit/secrets.toml
tmp/
.DS_Store
```

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

- [ ] **Step 3: Write a failing settings test**

Create `tests/test_config.py`:

```python
from travel_planner.config import Settings


def test_settings_do_not_require_observability_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "maps")
    monkeypatch.setenv("EXCHANGE_RATE_API_KEY", "fx")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-demo")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    settings = Settings()

    assert settings.google_maps_api_key.get_secret_value() == "maps"
    assert settings.langfuse_enabled is False
```

- [ ] **Step 4: Run the test to verify it fails**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'travel_planner'`.

- [ ] **Step 5: Implement environment settings**

Create `src/travel_planner/__init__.py` as an empty package file and `src/travel_planner/config.py`:

```python
from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_maps_api_key: SecretStr
    exchange_rate_api_key: SecretStr
    azure_openai_api_key: SecretStr
    azure_openai_endpoint: HttpUrl
    azure_openai_deployment: str
    azure_openai_api_version: str
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)
```

- [ ] **Step 6: Run tests and commit the bootstrap**

Run:

```bash
python -m pytest tests/test_config.py -q
git add pyproject.toml .env.example .gitignore src/travel_planner/__init__.py src/travel_planner/config.py tests/test_config.py
git commit -m "chore: bootstrap travel planner project"
```

Expected: `1 passed`, followed by a bootstrap commit.

## Task 2: Define Pace Presets And Domain Contracts

**Files:**
- Create: `src/travel_planner/domain/__init__.py`
- Create: `src/travel_planner/domain/pace.py`
- Create: `src/travel_planner/domain/models.py`
- Test: `tests/domain/test_pace.py`

- [ ] **Step 1: Write failing tests for the approved pace presets and verified price fields**

Create `tests/domain/test_pace.py`:

```python
from travel_planner.domain.models import PriceRecord, PriceStatus
from travel_planner.domain.pace import PaceLevel, get_pace_profile


def test_relaxed_profile_matches_not_too_tired_choice():
    profile = get_pace_profile(PaceLevel.RELAXED)

    assert profile.max_major_places_per_day == 2
    assert profile.max_required_transfer_minutes_per_day == 90
    assert profile.max_single_transfer_minutes == 35
    assert profile.walking_distance_warning_km == 6


def test_verified_price_keeps_source_and_original_currency():
    price = PriceRecord(
        item_id="usj-ticket",
        item_name="Universal Studios Japan Ticket",
        category="admission",
        amount_original=8600,
        currency_original="JPY",
        status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
        source_provider="Universal Studios Japan Official Website",
        source_url="https://www.usj.co.jp/web/en/us",
    )

    assert price.currency_original == "JPY"
    assert price.source_url.host == "www.usj.co.jp"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/domain/test_pace.py -q
```

Expected: FAIL because the domain package does not exist.

- [ ] **Step 3: Implement pace presets**

Create `src/travel_planner/domain/pace.py`:

```python
from enum import StrEnum
from pydantic import BaseModel


class PaceLevel(StrEnum):
    VERY_RELAXED = "VERY_RELAXED"
    RELAXED = "RELAXED"
    STANDARD = "STANDARD"
    INTENSIVE = "INTENSIVE"


class PaceProfile(BaseModel):
    level: PaceLevel
    max_major_places_per_day: int
    max_required_transfer_minutes_per_day: int
    max_single_transfer_minutes: int
    walking_distance_warning_km: float


PACE_PROFILES = {
    PaceLevel.VERY_RELAXED: PaceProfile(
        level=PaceLevel.VERY_RELAXED,
        max_major_places_per_day=2,
        max_required_transfer_minutes_per_day=75,
        max_single_transfer_minutes=30,
        walking_distance_warning_km=4,
    ),
    PaceLevel.RELAXED: PaceProfile(
        level=PaceLevel.RELAXED,
        max_major_places_per_day=2,
        max_required_transfer_minutes_per_day=90,
        max_single_transfer_minutes=35,
        walking_distance_warning_km=6,
    ),
    PaceLevel.STANDARD: PaceProfile(
        level=PaceLevel.STANDARD,
        max_major_places_per_day=3,
        max_required_transfer_minutes_per_day=120,
        max_single_transfer_minutes=50,
        walking_distance_warning_km=10,
    ),
    PaceLevel.INTENSIVE: PaceProfile(
        level=PaceLevel.INTENSIVE,
        max_major_places_per_day=4,
        max_required_transfer_minutes_per_day=180,
        max_single_transfer_minutes=75,
        walking_distance_warning_km=15,
    ),
}


def get_pace_profile(level: PaceLevel) -> PaceProfile:
    return PACE_PROFILES[level]
```

- [ ] **Step 4: Implement typed domain state**

Create `src/travel_planner/domain/models.py` with the contracts used by every later component:

```python
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pydantic import BaseModel, Field, HttpUrl
from travel_planner.domain.pace import PaceProfile


class PriceStatus(StrEnum):
    API_VERIFIED_EXACT = "API_VERIFIED_EXACT"
    USER_CONFIRMED_OFFICIAL_SOURCE = "USER_CONFIRMED_OFFICIAL_SOURCE"
    API_VERIFIED_RANGE = "API_VERIFIED_RANGE"
    MISSING_PRICE = "MISSING_PRICE"


class PriceRecord(BaseModel):
    item_id: str
    item_name: str
    category: str
    amount_original: Decimal | None = None
    amount_original_min: Decimal | None = None
    amount_original_max: Decimal | None = None
    currency_original: str
    status: PriceStatus
    source_provider: str
    source_url: HttpUrl | None = None
    retrieved_at: datetime | None = None


class ExchangeRateSnapshot(BaseModel):
    provider: str
    base_currency: str
    target_currency: str
    rate: Decimal
    retrieved_at: datetime
    rate_type: str = "INDICATIVE_MIDPOINT"


class PlaceStop(BaseModel):
    name: str
    place_id: str | None = None
    locked: bool = False
    load_tag: str = "FLEXIBLE_VISIT"


class TripSpec(BaseModel):
    destination: str
    days: int = Field(ge=1, le=5)
    budget_amount: Decimal
    budget_currency: str = "TWD"
    interests: list[str]
    pace: PaceProfile
    hotel: PlaceStop
    must_visit: list[PlaceStop] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
    fx_snapshot: ExchangeRateSnapshot | None = None
    budget_override_history: list[Decimal] = Field(default_factory=list)


class RouteEvidence(BaseModel):
    total_required_transfer_minutes: int
    max_single_transfer_minutes: int
    walking_distance_km: float
    encoded_polyline: str | None = None
    source_provider: str = "Google Routes API"


class DayPlanState(BaseModel):
    day: int
    status: str = "DRAFT"
    places: list[PlaceStop] = Field(default_factory=list)
    meals: list[PlaceStop] = Field(default_factory=list)
    route: RouteEvidence | None = None
    prices: list[PriceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retry_count: int = 0
    quality_score: int | None = None
```

- [ ] **Step 5: Run tests and commit domain contracts**

Run:

```bash
python -m pytest tests/domain/test_pace.py -q
git add src/travel_planner/domain tests/domain
git commit -m "feat: define trip state and pace profiles"
```

Expected: `2 passed`.

## Task 3: Create The Dedicated API Provider Registry

**Files:**
- Create: `src/travel_planner/integrations/__init__.py`
- Create: `src/travel_planner/integrations/api_registry.py`
- Test: `tests/integrations/test_api_registry.py`

- [ ] **Step 1: Write failing tests for provider metadata and secret isolation**

Create `tests/integrations/test_api_registry.py`:

```python
from travel_planner.integrations.api_registry import ProviderKey, get_provider


def test_registry_discloses_price_and_route_source_limitations():
    routes = get_provider(ProviderKey.GOOGLE_ROUTES)
    fx = get_provider(ProviderKey.EXCHANGE_RATE_API)

    assert "transit fare" in routes.limitations.lower()
    assert "estimated" in fx.limitations.lower()
    assert str(routes.docs_url).startswith("https://developers.google.com/")


def test_registry_never_stores_credentials():
    provider = get_provider(ProviderKey.GOOGLE_PLACES_NEW)

    serialized = provider.model_dump_json().lower()
    assert "api_key" not in serialized
    assert "secret" not in serialized
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python -m pytest tests/integrations/test_api_registry.py -q
```

Expected: FAIL because `api_registry` is not defined.

- [ ] **Step 3: Implement the sole source-of-truth registry**

Create `src/travel_planner/integrations/api_registry.py`:

```python
from enum import StrEnum
from pydantic import BaseModel, HttpUrl


class ProviderKey(StrEnum):
    GOOGLE_PLACES_NEW = "GOOGLE_PLACES_NEW"
    GOOGLE_ROUTES = "GOOGLE_ROUTES"
    EXCHANGE_RATE_API = "EXCHANGE_RATE_API"
    AZURE_OPENAI = "AZURE_OPENAI"
    LANGFUSE = "LANGFUSE"


class ProviderMetadata(BaseModel):
    key: ProviderKey
    display_name: str
    purpose: str
    docs_url: HttpUrl
    evidence_fields: list[str]
    limitations: str


PROVIDERS = {
    ProviderKey.GOOGLE_PLACES_NEW: ProviderMetadata(
        key=ProviderKey.GOOGLE_PLACES_NEW,
        display_name="Google Places API (New)",
        purpose="Ground places and restaurants to identifiers and coordinates.",
        docs_url="https://developers.google.com/maps/documentation/places/web-service/text-search",
        evidence_fields=["place_id", "location", "priceLevel", "priceRange"],
        limitations="Restaurant price fields may be absent or ranges, not checkout totals.",
    ),
    ProviderKey.GOOGLE_ROUTES: ProviderMetadata(
        key=ProviderKey.GOOGLE_ROUTES,
        display_name="Google Routes API",
        purpose="Validate travel duration, route shape, walking distance and available fare.",
        docs_url="https://developers.google.com/maps/documentation/routes",
        evidence_fields=["duration", "distanceMeters", "polyline", "transitFare"],
        limitations="Transit fare is only available on supported all-transit route results.",
    ),
    ProviderKey.EXCHANGE_RATE_API: ProviderMetadata(
        key=ProviderKey.EXCHANGE_RATE_API,
        display_name="ExchangeRate-API",
        purpose="Create a JPY/TWD conversion snapshot for one trip.",
        docs_url="https://www.exchangerate-api.com/docs/overview",
        evidence_fields=["conversion_rate", "time_last_update_utc"],
        limitations="Converted amounts are estimated budgets, not card settlement totals.",
    ),
    ProviderKey.AZURE_OPENAI: ProviderMetadata(
        key=ProviderKey.AZURE_OPENAI,
        display_name="Azure OpenAI",
        purpose="Generate agent proposals and review explanations.",
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
        evidence_fields=["model_deployment", "agent_output"],
        limitations="Generated recommendations require separate tool verification.",
    ),
    ProviderKey.LANGFUSE: ProviderMetadata(
        key=ProviderKey.LANGFUSE,
        display_name="Langfuse",
        purpose="Record traces, decisions and evaluation observations.",
        docs_url="https://langfuse.com/docs/observability/overview",
        evidence_fields=["trace_id", "observation_type"],
        limitations="Telemetry failure must not turn unverified plans into approved plans.",
    ),
}


def get_provider(key: ProviderKey) -> ProviderMetadata:
    return PROVIDERS[key]
```

- [ ] **Step 4: Run tests and commit the API registry**

Run:

```bash
python -m pytest tests/integrations/test_api_registry.py -q
git add src/travel_planner/integrations tests/integrations
git commit -m "feat: centralize external API provenance metadata"
```

Expected: `2 passed`. This commit satisfies the requirement that API provenance be maintained in one dedicated file.

## Task 4: Implement Price Conversion And Budget Gate

**Files:**
- Create: `src/travel_planner/validation/__init__.py`
- Create: `src/travel_planner/validation/budget.py`
- Test: `tests/validation/test_budget.py`

- [ ] **Step 1: Write tests for exact, range, missing, and budget-override paths**

Create `tests/validation/test_budget.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal
from travel_planner.domain.models import ExchangeRateSnapshot, PriceRecord, PriceStatus
from travel_planner.validation.budget import BudgetStatus, evaluate_budget


FX = ExchangeRateSnapshot(
    provider="ExchangeRate-API",
    base_currency="JPY",
    target_currency="TWD",
    rate=Decimal("0.20"),
    retrieved_at=datetime.now(UTC),
)


def test_range_crossing_limit_is_possible_over_budget():
    prices = [
        PriceRecord(
            item_id="hotel", item_name="Hotel", category="lodging",
            amount_original=Decimal("100000"), currency_original="JPY",
            status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE, source_provider="Hotel official",
        ),
        PriceRecord(
            item_id="dinner", item_name="Dinner", category="meal",
            amount_original_min=Decimal("20000"), amount_original_max=Decimal("30000"),
            currency_original="JPY", status=PriceStatus.API_VERIFIED_RANGE,
            source_provider="Google Places API (New)",
        ),
    ]

    outcome = evaluate_budget(prices, FX, Decimal("25000"))

    assert outcome.status is BudgetStatus.POSSIBLE_OVER_BUDGET
    assert outcome.confirmed_total == Decimal("20000.00")
    assert outcome.maximum_total == Decimal("26000.00")


def test_missing_price_blocks_verified_pass():
    outcome = evaluate_budget(
        [PriceRecord(
            item_id="fare", item_name="Transit", category="transport",
            currency_original="JPY", status=PriceStatus.MISSING_PRICE,
            source_provider="Google Routes API",
        )],
        FX,
        Decimal("25000"),
    )

    assert outcome.status is BudgetStatus.MISSING_PRICE
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python -m pytest tests/validation/test_budget.py -q
```

Expected: FAIL because budget validation does not exist.

- [ ] **Step 3: Implement deterministic budget evaluation**

Create `src/travel_planner/validation/budget.py`:

```python
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from pydantic import BaseModel
from travel_planner.domain.models import ExchangeRateSnapshot, PriceRecord, PriceStatus


class BudgetStatus(StrEnum):
    PASSED = "PASSED"
    OVER_BUDGET = "OVER_BUDGET"
    POSSIBLE_OVER_BUDGET = "POSSIBLE_OVER_BUDGET"
    MISSING_PRICE = "MISSING_PRICE"


class BudgetOutcome(BaseModel):
    status: BudgetStatus
    confirmed_total: Decimal
    minimum_total: Decimal
    maximum_total: Decimal
    missing_item_ids: list[str]


def _converted(amount: Decimal, fx: ExchangeRateSnapshot) -> Decimal:
    return (amount * fx.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def evaluate_budget(
    prices: list[PriceRecord], fx: ExchangeRateSnapshot, limit: Decimal
) -> BudgetOutcome:
    confirmed = Decimal("0")
    minimum = Decimal("0")
    maximum = Decimal("0")
    missing: list[str] = []
    for price in prices:
        if price.status is PriceStatus.MISSING_PRICE:
            missing.append(price.item_id)
        elif price.status is PriceStatus.API_VERIFIED_RANGE:
            minimum += _converted(price.amount_original_min or Decimal("0"), fx)
            maximum += _converted(price.amount_original_max or Decimal("0"), fx)
        else:
            exact = _converted(price.amount_original or Decimal("0"), fx)
            confirmed += exact
            minimum += exact
            maximum += exact
    if missing:
        status = BudgetStatus.MISSING_PRICE
    elif confirmed > limit:
        status = BudgetStatus.OVER_BUDGET
    elif maximum > limit:
        status = BudgetStatus.POSSIBLE_OVER_BUDGET
    else:
        status = BudgetStatus.PASSED
    return BudgetOutcome(
        status=status,
        confirmed_total=confirmed,
        minimum_total=minimum,
        maximum_total=maximum,
        missing_item_ids=missing,
    )
```

- [ ] **Step 4: Run tests and commit**

Run:

```bash
python -m pytest tests/validation/test_budget.py -q
git add src/travel_planner/validation tests/validation
git commit -m "feat: evaluate verified and estimated trip costs"
```

Expected: `2 passed`.

## Task 5: Implement Pace Gate And User Decision Contracts

**Files:**
- Modify: `src/travel_planner/domain/models.py`
- Create: `src/travel_planner/validation/pace.py`
- Test: `tests/validation/test_pace.py`

- [ ] **Step 1: Write failing tests for pace conflicts and accepted overrides**

Create `tests/validation/test_pace.py`:

```python
from travel_planner.domain.models import PlaceStop, RouteEvidence
from travel_planner.domain.pace import PaceLevel, get_pace_profile
from travel_planner.validation.pace import PaceStatus, evaluate_pace


def test_relaxed_route_above_ninety_minutes_creates_blocking_conflict():
    result = evaluate_pace(
        places=[PlaceStop(name="Osaka Castle"), PlaceStop(name="Kaiyukan")],
        route=RouteEvidence(
            total_required_transfer_minutes=142,
            max_single_transfer_minutes=56,
            walking_distance_km=4.2,
        ),
        profile=get_pace_profile(PaceLevel.RELAXED),
    )

    assert result.status is PaceStatus.CONFLICT
    assert "total_required_transfer_minutes" in result.reasons


def test_full_day_high_load_rejects_second_major_place():
    result = evaluate_pace(
        places=[
            PlaceStop(name="Universal Studios Japan", load_tag="FULL_DAY_HIGH_LOAD"),
            PlaceStop(name="Dotonbori"),
        ],
        route=RouteEvidence(
            total_required_transfer_minutes=45,
            max_single_transfer_minutes=20,
            walking_distance_km=2,
        ),
        profile=get_pace_profile(PaceLevel.STANDARD),
    )

    assert result.status is PaceStatus.CONFLICT
    assert "full_day_high_load" in result.reasons
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
python -m pytest tests/validation/test_pace.py -q
```

Expected: FAIL because `evaluate_pace` is missing.

- [ ] **Step 3: Add decision types to the domain contracts**

Append to `src/travel_planner/domain/models.py`:

```python
class ConflictType(StrEnum):
    PACE_EXCEEDED = "PACE_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    PRICE_MISSING = "PRICE_MISSING"
    GROUNDING_FAILED = "GROUNDING_FAILED"


class UserChoice(StrEnum):
    KEEP_PACE_REPLAN = "KEEP_PACE_REPLAN"
    ACCEPT_PACE_WARNING = "ACCEPT_PACE_WARNING"
    INCREASE_DAY_PACE = "INCREASE_DAY_PACE"
    INCREASE_BUDGET_KEEP_PLAN = "INCREASE_BUDGET_KEEP_PLAN"
    KEEP_LOCKED_REDUCE_COST = "KEEP_LOCKED_REDUCE_COST"
    REPLACE_ITEMS_KEEP_BUDGET = "REPLACE_ITEMS_KEEP_BUDGET"
    ACCEPT_COST_WARNING = "ACCEPT_COST_WARNING"


class ConflictEvent(BaseModel):
    conflict_type: ConflictType
    day: int
    reasons: list[str]
    evidence: dict[str, object]
    status: str = "AWAITING_USER_DECISION"


class UserDecision(BaseModel):
    choice: UserChoice
    new_budget_limit: Decimal | None = None
    new_pace: PaceProfile | None = None
```

- [ ] **Step 4: Implement pace evaluation**

Create `src/travel_planner/validation/pace.py`:

```python
from enum import StrEnum
from pydantic import BaseModel
from travel_planner.domain.models import PlaceStop, RouteEvidence
from travel_planner.domain.pace import PaceProfile


class PaceStatus(StrEnum):
    PASSED = "PASSED"
    CONFLICT = "CONFLICT"
    WARNING = "WARNING"


class PaceOutcome(BaseModel):
    status: PaceStatus
    reasons: list[str]


def evaluate_pace(
    places: list[PlaceStop], route: RouteEvidence, profile: PaceProfile
) -> PaceOutcome:
    reasons: list[str] = []
    if len(places) > profile.max_major_places_per_day:
        reasons.append("major_place_count")
    if route.total_required_transfer_minutes > profile.max_required_transfer_minutes_per_day:
        reasons.append("total_required_transfer_minutes")
    if route.max_single_transfer_minutes > profile.max_single_transfer_minutes:
        reasons.append("max_single_transfer_minutes")
    if any(place.load_tag == "FULL_DAY_HIGH_LOAD" for place in places) and len(places) > 1:
        reasons.append("full_day_high_load")
    status = PaceStatus.CONFLICT if reasons else PaceStatus.PASSED
    if not reasons and route.walking_distance_km > profile.walking_distance_warning_km:
        return PaceOutcome(status=PaceStatus.WARNING, reasons=["walking_distance_warning"])
    return PaceOutcome(status=status, reasons=reasons)
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python -m pytest tests/validation/test_pace.py tests/domain/test_pace.py -q
git add src/travel_planner/domain/models.py src/travel_planner/validation/pace.py tests/validation/test_pace.py
git commit -m "feat: gate daily plans by pace and conflict decisions"
```

Expected: all four tests PASS.

## Task 6: Add Real Places, Routes, And Exchange-Rate Adapters

**Files:**
- Create: `src/travel_planner/integrations/google_places.py`
- Create: `src/travel_planner/integrations/google_routes.py`
- Create: `src/travel_planner/integrations/exchange_rates.py`
- Test: `tests/integrations/test_google_places.py`
- Test: `tests/integrations/test_google_routes.py`
- Test: `tests/integrations/test_exchange_rates.py`

- [ ] **Step 1: Write mocked contract tests for external responses**

Create tests that intercept `httpx.Client.post/get` and assert field parsing:

```python
# tests/integrations/test_google_routes.py
from travel_planner.integrations.google_routes import GoogleRoutesClient


def test_route_without_transit_fare_is_valid_but_has_no_verified_price(httpx_mock):
    httpx_mock.add_response(
        url="https://routes.googleapis.com/directions/v2:computeRoutes",
        json={"routes": [{
            "duration": "3120s",
            "distanceMeters": 6200,
            "polyline": {"encodedPolyline": "route"},
            "legs": [{"duration": "1380s"}, {"duration": "1740s"}],
        }]},
    )
    route = GoogleRoutesClient("maps").compute_daily_route(["hotel", "poi", "hotel"])

    assert route.evidence.total_required_transfer_minutes == 52
    assert route.transit_fare is None
```

```python
# tests/integrations/test_google_places.py
from travel_planner.integrations.google_places import GooglePlacesClient


def test_text_search_grounds_first_place_candidate(httpx_mock):
    httpx_mock.add_response(
        url="https://places.googleapis.com/v1/places:searchText",
        json={"places": [{"id": "place123", "displayName": {"text": "Dotonbori"}}]},
    )
    place = GooglePlacesClient("maps").ground("Dotonbori Osaka Japan")

    assert place.place_id == "place123"
```

```python
# tests/integrations/test_exchange_rates.py
from decimal import Decimal
from travel_planner.integrations.exchange_rates import ExchangeRateClient


def test_fx_client_saves_rate_snapshot_timestamp(httpx_mock):
    httpx_mock.add_response(
        url="https://v6.exchangerate-api.com/v6/key/pair/JPY/TWD",
        json={"result": "success", "conversion_rate": 0.2,
              "time_last_update_utc": "Sat, 23 May 2026 00:00:01 +0000"},
    )
    snapshot = ExchangeRateClient("key").snapshot("JPY", "TWD")

    assert snapshot.rate == Decimal("0.2")
    assert snapshot.provider == "ExchangeRate-API"
```

Add `pytest-httpx>=0.30` to the `dev` dependencies in `pyproject.toml`.

- [ ] **Step 2: Run the adapter tests to confirm failure**

Run:

```bash
python -m pytest tests/integrations/test_google_places.py tests/integrations/test_google_routes.py tests/integrations/test_exchange_rates.py -q
```

Expected: FAIL because clients do not exist.

- [ ] **Step 3: Implement thin HTTP clients with explicit field masks**

Implement `GooglePlacesClient.ground()` using:

```python
class GroundingNotFound(Exception):
    def __init__(self, query: str):
        super().__init__(f"No verified place found for {query}")
        self.query = query


response = self.client.post(
    "https://places.googleapis.com/v1/places:searchText",
    headers={
        "X-Goog-Api-Key": self.api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.priceLevel,places.priceRange",
    },
    json={"textQuery": query, "languageCode": "zh-TW"},
)
```

Return a `PlaceStop`; raise a typed `GroundingNotFound(query)` when `places` is empty.

Implement `GoogleRoutesClient.compute_daily_route()` using:

```python
class RouteUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


response = self.client.post(
    "https://routes.googleapis.com/directions/v2:computeRoutes",
    headers={
        "X-Goog-Api-Key": self.api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.legs.duration,routes.travelAdvisory.transitFare",
    },
    json={
        "origin": {"placeId": place_ids[0]},
        "destination": {"placeId": place_ids[-1]},
        "intermediates": [{"placeId": place_id} for place_id in place_ids[1:-1]],
        "travelMode": "TRANSIT",
        "computeAlternativeRoutes": False,
        "languageCode": "zh-TW",
    },
)
```

Raise `RouteUnavailable("No Google Routes result for verified stops")` when `routes` is empty. Convert seconds to rounded-up minutes; return `RouteEvidence` plus an optional `PriceRecord` for fare only when the response contains a fare.

Implement `ExchangeRateClient.snapshot()` using the pair endpoint and produce `ExchangeRateSnapshot` with the source timestamp.

- [ ] **Step 4: Run mocked tests and a credentials-guarded live smoke test**

Run:

```bash
python -m pytest tests/integrations -q
```

Expected: adapter contract tests PASS. Add live tests marked `@pytest.mark.live_api` and skipped unless the corresponding key exists; do not make live calls part of the default test suite.

- [ ] **Step 5: Commit adapters**

```bash
git add pyproject.toml src/travel_planner/integrations tests/integrations
git commit -m "feat: integrate verified travel and exchange data providers"
```

## Task 7: Add Agent Skill Specifications And Structured CrewAI Runners

**Files:**
- Create: `src/travel_planner/agents/__init__.py`
- Create: `src/travel_planner/agents/skills/itinerary_agent.md`
- Create: `src/travel_planner/agents/skills/food_agent.md`
- Create: `src/travel_planner/agents/skills/reviewer_agent.md`
- Create: `src/travel_planner/agents/runner.py`
- Test: `tests/agents/test_runner.py`

- [ ] **Step 1: Write a failing runner test using a fake structured LLM**

Create `tests/agents/test_runner.py`:

```python
from travel_planner.agents.runner import AgentRunner, ItineraryProposal


class FakeLlm:
    def call(self, prompt: str):
        assert "RELAXED" in prompt
        return ItineraryProposal(candidates=[["Osaka Castle", "Dotonbori"]])


def test_itinerary_prompt_contains_trip_constraints():
    runner = AgentRunner(llm=FakeLlm())

    proposal = runner.propose_itinerary(
        destination="Osaka", pace_name="RELAXED", visited=[], rejected=[]
    )

    assert proposal.candidates == [["Osaka Castle", "Dotonbori"]]
```

- [ ] **Step 2: Write the Agent skill files**

`itinerary_agent.md` must state:

```markdown
# Itinerary Agent
Return 2-3 geographically concentrated candidate routes.
Never repeat visited or rejected place names.
For RELAXED pace, propose at most two major places.
Do not invent place IDs, prices, travel minutes, or verification results.
```

`food_agent.md` must state:

```markdown
# Food Agent
Recommend restaurant candidates near verified route stops and matching food interests.
Do not claim a restaurant exists, is open, or has an exact price; tools verify those fields.
```

`reviewer_agent.md` must state:

```markdown
# Reviewer Agent
Explain validated results and assign a 1-5 quality score.
Never override tool verdicts, accepted warnings, source provenance, or calculated totals.
```

- [ ] **Step 3: Implement a structured runner boundary**

In `src/travel_planner/agents/runner.py`, define output models and inject the LLM so workflow tests do not call Azure:

```python
from pathlib import Path
from pydantic import BaseModel, Field


class ItineraryProposal(BaseModel):
    candidates: list[list[str]] = Field(min_length=2, max_length=3)


class FoodProposal(BaseModel):
    lunch_candidates: list[str]
    dinner_candidates: list[str]


class ReviewResult(BaseModel):
    score: int = Field(ge=1, le=5)
    summary: str
    warnings: list[str]


class AgentRunner:
    def __init__(self, llm):
        self.llm = llm

    def propose_itinerary(self, destination: str, pace_name: str, visited: list[str], rejected: list[str]):
        skill = Path(__file__).with_name("skills").joinpath("itinerary_agent.md").read_text()
        prompt = f"{skill}\nDestination: {destination}\nPace: {pace_name}\nVisited: {visited}\nRejected: {rejected}"
        return self.llm.call(prompt)
```

Add a factory that constructs `crewai.LLM` for Azure using `Settings` and a Pydantic response format for each role; the deterministic workflow only depends on `AgentRunner`:

```python
from crewai import LLM
from travel_planner.config import Settings


def build_azure_llm(settings: Settings, response_format: type[BaseModel]) -> LLM:
    return LLM(
        model=f"azure/{settings.azure_openai_deployment}",
        api_key=settings.azure_openai_api_key.get_secret_value(),
        base_url=str(settings.azure_openai_endpoint),
        api_version=settings.azure_openai_api_version,
        response_format=response_format,
        temperature=0.2,
        timeout=60.0,
        max_retries=2,
    )
```

- [ ] **Step 4: Run tests and commit**

Run:

```bash
python -m pytest tests/agents/test_runner.py -q
git add src/travel_planner/agents tests/agents
git commit -m "feat: add scoped agent skills and structured proposals"
```

Expected: runner test PASS; no real Azure call is executed in tests.

## Task 8: Implement The Orchestrator State Machine And Both Gates

**Files:**
- Create: `src/travel_planner/workflow/__init__.py`
- Create: `src/travel_planner/workflow/orchestrator.py`
- Test: `tests/workflow/test_orchestrator.py`

- [ ] **Step 1: Write workflow tests for the two required demo branches**

Create `tests/workflow/test_orchestrator.py` with fake agents and tools:

```python
from decimal import Decimal
from travel_planner.domain.models import UserChoice, UserDecision
from travel_planner.workflow.orchestrator import DemoFixtures, WorkflowStatus, TravelWorkflow


def test_pace_conflict_pauses_then_replans_with_constraints():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.first_route_too_tiring())

    state = workflow.start_day(2)

    assert state.status is WorkflowStatus.AWAITING_PACE_DECISION
    replanned = workflow.resume(UserDecision(choice=UserChoice.KEEP_PACE_REPLAN))
    assert replanned.day_state.route.total_required_transfer_minutes <= 90
    assert "PACE_REPLANNED" in replanned.day_state.warnings


def test_increase_budget_keeps_route_and_moves_to_review():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.possible_budget_overrun())
    blocked = workflow.start_day(2)
    route_before = blocked.day_state.route

    completed = workflow.resume(UserDecision(
        choice=UserChoice.INCREASE_BUDGET_KEEP_PLAN,
        new_budget_limit=Decimal("28000"),
    ))

    assert completed.status is WorkflowStatus.DAY_APPROVED
    assert completed.day_state.route == route_before
    assert "USER_APPROVED_BUDGET_INCREASE" in completed.day_state.warnings
```

Use fake adapters only in these tests; `DemoFixtures` is a test helper, not a production fallback.

- [ ] **Step 2: Run workflow tests to see them fail**

Run:

```bash
python -m pytest tests/workflow/test_orchestrator.py -q
```

Expected: FAIL because the orchestrator does not exist.

- [ ] **Step 3: Implement explicit state-machine phases**

Create `src/travel_planner/workflow/orchestrator.py` with:

```python
from enum import StrEnum
from pydantic import BaseModel
from travel_planner.domain.models import (
    ConflictEvent,
    ConflictType,
    DayPlanState,
    TripSpec,
    UserChoice,
    UserDecision,
)
from travel_planner.validation.budget import BudgetStatus, evaluate_budget
from travel_planner.validation.pace import PaceStatus, evaluate_pace


class WorkflowStatus(StrEnum):
    PLANNING = "PLANNING"
    AWAITING_PACE_DECISION = "AWAITING_PACE_DECISION"
    AWAITING_PRICE_DECISION = "AWAITING_PRICE_DECISION"
    AWAITING_BUDGET_DECISION = "AWAITING_BUDGET_DECISION"
    DAY_APPROVED = "DAY_APPROVED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class WorkflowResult(BaseModel):
    status: WorkflowStatus
    day_state: DayPlanState
    conflict: ConflictEvent | None = None


class TravelWorkflow:
    def __init__(self, trip_spec, agents, places, routes, price_collector, reviewer, tracer):
        self.trip_spec: TripSpec = trip_spec
        self.agents = agents
        self.places = places
        self.routes = routes
        self.price_collector = price_collector
        self.reviewer = reviewer
        self.tracer = tracer
        self.current_day: DayPlanState | None = None
        self.replanning_constraints: list[str] = []
        self.pending_warnings: list[str] = []

    def start_day(self, day: int) -> WorkflowResult:
        candidate_names = self.agents.propose_itinerary(
            self.trip_spec.destination,
            self.trip_spec.pace.level.value,
            visited=[],
            rejected=self.replanning_constraints,
        ).candidates[0]
        try:
            places = [self.places.ground(name) for name in candidate_names]
        except GroundingNotFound as error:
            previous_retries = self.current_day.retry_count if self.current_day else 0
            retries = previous_retries + 1
            self.replanning_constraints.append(f"REJECT_PLACE:{error.query}")
            failed = DayPlanState(day=day, retry_count=retries, warnings=["GROUNDING_FAILED"])
            self.current_day = failed
            if retries >= 2:
                return WorkflowResult(status=WorkflowStatus.NEEDS_MANUAL_REVIEW, day_state=failed)
            return self.start_day(day)
        self.current_day = DayPlanState(
            day=day, places=places, warnings=self.pending_warnings, retry_count=0
        )
        self.pending_warnings = []
        return self._route_and_pace_then_complete()

    def _route_and_pace_then_complete(self) -> WorkflowResult:
        route = self.routes.compute_daily_route(
            [self.trip_spec.hotel.place_id]
            + [place.place_id for place in self.current_day.places]
            + [self.trip_spec.hotel.place_id]
        )
        self.current_day.route = route.evidence
        initial = evaluate_pace(self.current_day.places, route.evidence, self.trip_spec.pace)
        if initial.status is PaceStatus.CONFLICT:
            return self._pause_for_pace(initial.reasons, "initial_route")

        meal_names = self.agents.propose_food(self.current_day.places).lunch_candidates[:1]
        self.current_day.meals = [self.places.ground(name) for name in meal_names]
        full_route = self.routes.compute_daily_route(
            [self.trip_spec.hotel.place_id]
            + [place.place_id for place in self.current_day.places + self.current_day.meals]
            + [self.trip_spec.hotel.place_id]
        )
        self.current_day.route = full_route.evidence
        final = evaluate_pace(
            self.current_day.places,
            full_route.evidence,
            self.trip_spec.pace,
        )
        if final.status is PaceStatus.CONFLICT:
            return self._pause_for_pace(final.reasons, "restaurant_included_final_route")
        self.current_day.prices = self.price_collector.collect(self.current_day, full_route)
        return self._run_budget_gate()

    def _pause_for_pace(self, reasons: list[str], phase: str) -> WorkflowResult:
        conflict = ConflictEvent(
            conflict_type=ConflictType.PACE_EXCEEDED,
            day=self.current_day.day,
            reasons=reasons,
            evidence={"phase": phase, "route": self.current_day.route.model_dump()},
        )
        return WorkflowResult(
            status=WorkflowStatus.AWAITING_PACE_DECISION,
            day_state=self.current_day,
            conflict=conflict,
        )

    def _run_budget_gate(self) -> WorkflowResult:
        outcome = evaluate_budget(
            self.trip_spec.prices + self.current_day.prices,
            self.trip_spec.fx_snapshot,
            self.trip_spec.budget_override_history[-1]
            if self.trip_spec.budget_override_history
            else self.trip_spec.budget_amount,
        )
        if outcome.status is not BudgetStatus.PASSED:
            conflict_type = (
                ConflictType.PRICE_MISSING
                if outcome.status is BudgetStatus.MISSING_PRICE
                else ConflictType.BUDGET_EXCEEDED
            )
            conflict = ConflictEvent(
                conflict_type=conflict_type,
                day=self.current_day.day,
                reasons=[outcome.status.value],
                evidence=outcome.model_dump(),
            )
            return WorkflowResult(
                status=(
                    WorkflowStatus.AWAITING_PRICE_DECISION
                    if outcome.status is BudgetStatus.MISSING_PRICE
                    else WorkflowStatus.AWAITING_BUDGET_DECISION
                ),
                day_state=self.current_day,
                conflict=conflict,
            )
        return self._review_and_commit_without_replanning()

    def _review_and_commit_without_replanning(self) -> WorkflowResult:
        review = self.reviewer.review(self.current_day)
        self.current_day.quality_score = review.score
        self.current_day.warnings.extend(review.warnings)
        self.current_day.status = "APPROVED"
        self.tracer.event("day_state_committed", self.current_day.model_dump(mode="json"))
        return WorkflowResult(status=WorkflowStatus.DAY_APPROVED, day_state=self.current_day)

    def resume(self, decision: UserDecision) -> WorkflowResult:
        if decision.choice is UserChoice.INCREASE_BUDGET_KEEP_PLAN:
            if decision.new_budget_limit is None:
                raise ValueError("new_budget_limit is required for a budget increase")
            self.trip_spec.budget_override_history.append(decision.new_budget_limit)
            self.current_day.warnings.append("USER_APPROVED_BUDGET_INCREASE")
            return self._review_and_commit_without_replanning()
        if decision.choice is UserChoice.KEEP_PACE_REPLAN:
            self.replanning_constraints = ["KEEP_PACE_LIMITS", "CONCENTRATE_AREA"]
            self.pending_warnings.append("PACE_REPLANNED")
            return self.start_day(self.current_day.day)
        if decision.choice is UserChoice.ACCEPT_PACE_WARNING:
            self.current_day.warnings.append("USER_ACCEPTED_INTENSIVE_DAY")
            return self._run_budget_gate()
        if decision.choice is UserChoice.INCREASE_DAY_PACE:
            self.trip_spec.pace = decision.new_pace
            return self._route_and_pace_then_complete()
        if decision.choice in {
            UserChoice.KEEP_LOCKED_REDUCE_COST,
            UserChoice.REPLACE_ITEMS_KEEP_BUDGET,
        }:
            self.replanning_constraints = [decision.choice.value]
            return self.start_day(self.current_day.day)
        if decision.choice is UserChoice.ACCEPT_COST_WARNING:
            self.current_day.warnings.append("UNVERIFIED_COST_ACCEPTED")
            return self._review_and_commit_without_replanning()
        raise ValueError(f"Unsupported user decision: {decision.choice}")
```

Import `GroundingNotFound` from `travel_planner.integrations.google_places`. Add the matching route-unavailable exception branch around `compute_daily_route()` using the same `retry_count >= 2` transition to `NEEDS_MANUAL_REVIEW`; route tests in the next step exercise that boundary. Do not embed HTTP or prompt logic in this module: all dependencies remain constructor-injected clients and agent runner instances.

- [ ] **Step 4: Add route re-validation after food stops**

Add a third test:

```python
def test_restaurant_detour_can_trigger_final_pace_conflict():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.restaurant_causes_detour())

    result = workflow.start_day(1)

    assert result.status is WorkflowStatus.AWAITING_PACE_DECISION
    assert "restaurant_included_final_route" in result.conflict.evidence
```

Implement the second pace evaluation after restaurant grounding and final route computation so this test passes.

Add a retry-boundary test and the explicit route exception branch:

```python
def test_second_unroutable_candidate_requires_manual_review():
    workflow = TravelWorkflow.from_fixtures(DemoFixtures.routes_fail_twice())

    result = workflow.start_day(3)

    assert result.status is WorkflowStatus.NEEDS_MANUAL_REVIEW
    assert result.day_state.retry_count == 2
```

```python
try:
    route = self.routes.compute_daily_route(place_ids)
except RouteUnavailable as error:
    retries = self.current_day.retry_count + 1
    self.replanning_constraints.append(f"REJECT_ROUTE:{error.reason}")
    self.current_day.retry_count = retries
    self.current_day.warnings.append("ROUTE_UNAVAILABLE")
    if retries >= 2:
        return WorkflowResult(
            status=WorkflowStatus.NEEDS_MANUAL_REVIEW,
            day_state=self.current_day,
        )
    return self.start_day(self.current_day.day)
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python -m pytest tests/workflow/test_orchestrator.py tests/validation -q
git add src/travel_planner/workflow tests/workflow
git commit -m "feat: orchestrate verification gates and user decisions"
```

Expected: pace replan, budget accept-without-replan, and restaurant detour tests PASS.

## Task 9: Add Langfuse Observability Without Making It A Verification Dependency

**Files:**
- Create: `src/travel_planner/observability/__init__.py`
- Create: `src/travel_planner/observability/tracing.py`
- Modify: `src/travel_planner/workflow/orchestrator.py`
- Test: `tests/observability/test_tracing.py`

- [ ] **Step 1: Write tests for trace events and no-op behavior**

Create `tests/observability/test_tracing.py`:

```python
from travel_planner.observability.tracing import NoOpTracer, RecordingTracer


def test_recording_tracer_records_gate_decision_evidence():
    tracer = RecordingTracer()
    tracer.event("pace_conflict", {"day": 2, "minutes": 142, "limit": 90})

    assert tracer.events[0]["name"] == "pace_conflict"


def test_noop_tracer_never_blocks_workflow():
    NoOpTracer().event("budget_conflict", {"day": 2})
```

- [ ] **Step 2: Implement trace interface and Langfuse adapter**

Create a `Tracer` protocol with `event(name, payload)` and `span(name)` operations. Implement:

```python
class NoOpTracer:
    def event(self, name: str, payload: dict) -> None:
        return None


class RecordingTracer:
    def __init__(self):
        self.events: list[dict] = []

    def event(self, name: str, payload: dict) -> None:
        self.events.append({"name": name, "payload": payload})
```

Implement `LangfuseTracer` using the current Langfuse observation API:

```python
from langfuse import get_client


class LangfuseTracer:
    def __init__(self):
        self.client = get_client()

    def event(self, name: str, payload: dict) -> None:
        with self.client.start_as_current_observation(as_type="event", name=name) as event:
            event.update(input=payload)
```

Inject the tracer into `TravelWorkflow`, recording API evidence identifiers, validation outcomes, conflicts, user decisions, retries, and review scores.

- [ ] **Step 3: Run tests and commit**

Run:

```bash
python -m pytest tests/observability tests/workflow -q
git add src/travel_planner/observability src/travel_planner/workflow/orchestrator.py tests/observability
git commit -m "feat: trace workflow verification and human decisions"
```

Expected: all observability and workflow tests PASS even when Langfuse is disabled.

## Task 10: Build Streamlit Input, Decision, Evidence, And Route Views

**Files:**
- Create: `src/travel_planner/ui/__init__.py`
- Create: `src/travel_planner/ui/app.py`
- Create: `src/travel_planner/ui/map_component.py`
- Test: `tests/ui/test_view_models.py`

- [ ] **Step 1: Write tests for user-facing conflict text and source disclosure**

Create `tests/ui/test_view_models.py`:

```python
from travel_planner.ui.app import format_pace_conflict, format_price_source


def test_pace_conflict_displays_observed_and_selected_limit():
    message = format_pace_conflict(observed_minutes=142, limit_minutes=90)
    assert "142" in message
    assert "90" in message
    assert "悠閒" in message


def test_price_source_displays_original_currency_and_provider():
    text = format_price_source("JPY 8,600", "Universal Studios Japan Official Website")
    assert "JPY 8,600" in text
    assert "Universal Studios Japan Official Website" in text
```

- [ ] **Step 2: Implement view-model formatters and session-state screens**

`app.py` must contain four UI states backed by `st.session_state.workflow_result`:

```python
def format_pace_conflict(observed_minutes: int, limit_minutes: int) -> str:
    return f"此日必要移動 {observed_minutes} 分鐘，超過悠閒模式上限 {limit_minutes} 分鐘。"


def format_price_source(original_price: str, provider: str) -> str:
    return f"{original_price} | 資料來源：{provider}"
```

Screens:

1. `render_trip_spec_form()` captures budget currency, hotel and official price URL, must-visit price, and four pace choices.
2. `render_running_or_evidence()` displays Agent/tool phase chips and source provider metadata from `api_registry.py`.
3. `render_decision_gate()` presents pace and budget decisions; selecting increase budget calls `resume()` and does not re-create route candidates.
4. `render_approved_itinerary()` displays route timing, costs in JPY and TWD, FX timestamp, warnings, evaluation score, and map.

- [ ] **Step 3: Implement route map component**

Create `map_component.py` using `streamlit.components.v1.html`. The component accepts only verified stops and encoded polyline. It loads the Maps JavaScript API using the runtime key and draws markers plus decoded route polyline; it must never request map data for unverified place names.

- [ ] **Step 4: Run tests and manually launch Streamlit**

Run:

```bash
python -m pytest tests/ui -q
python -m streamlit run src/travel_planner/ui/app.py --server.port 8501
```

Expected: tests PASS and the app is reachable at `http://localhost:8501`. With credentials configured, the initial form proceeds to live verification; without credentials, settings validation reports missing environment values.

- [ ] **Step 5: Commit UI**

```bash
git add src/travel_planner/ui tests/ui
git commit -m "feat: present itinerary decisions and verified evidence"
```

## Task 11: Add Live API Smoke Checks, Documentation, And Demo Acceptance Test

**Files:**
- Create: `tests/live/test_osaka_api_smoke.py`
- Create: `tests/workflow/test_demo_acceptance.py`
- Create: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Add credentials-guarded live smoke tests**

Create `tests/live/test_osaka_api_smoke.py`:

```python
import os
import pytest
from travel_planner.integrations.google_places import GooglePlacesClient


@pytest.mark.live_api
@pytest.mark.skipif(not os.getenv("GOOGLE_MAPS_API_KEY"), reason="requires Google Maps key")
def test_osaka_dotonbori_can_be_grounded_live():
    place = GooglePlacesClient(os.environ["GOOGLE_MAPS_API_KEY"]).ground("Dotonbori Osaka Japan")
    assert place.place_id
```

Add separate live smoke tests for a JPY/TWD exchange snapshot and one verified Osaka transit route, each skipped unless its key exists.

- [ ] **Step 2: Add deterministic end-to-end acceptance test**

Create `tests/workflow/test_demo_acceptance.py` using fake adapters that mirror the Demo script:

```python
def test_demo_shows_pace_replan_then_budget_override_without_second_replan():
    workflow = build_scripted_demo_workflow()

    pace_conflict = workflow.start_day(2)
    assert pace_conflict.status.value == "AWAITING_PACE_DECISION"

    budget_conflict = workflow.resume_keep_pace_and_replan()
    assert budget_conflict.status.value == "AWAITING_BUDGET_DECISION"

    approved = workflow.resume_increase_budget(28000)
    assert approved.status.value == "DAY_APPROVED"
    assert "USER_APPROVED_BUDGET_INCREASE" in approved.day_state.warnings
```

The deterministic acceptance path tests logic only; the separate `live_api` tests establish that configured providers are reachable before the presentation.

- [ ] **Step 3: Write README setup and provenance instructions**

`README.md` must cover:

```markdown
# Multi-Agent Travel Planner

## Setup
1. Install Python dependencies.
2. Create `.env` from `.env.example`.
3. Supply Google Maps, ExchangeRate-API, Azure OpenAI, and optional Langfuse credentials.
4. Run `python -m streamlit run src/travel_planner/ui/app.py`.

## Data provenance
- Places and restaurant grounding: Google Places API (New).
- Route duration and available transit fare: Google Routes API.
- Currency snapshot: ExchangeRate-API; converted TWD amounts are estimates.
- Admission/lodging exact costs: user-confirmed official URLs.
- Agent output: Azure OpenAI suggestions, never treated as verified facts without tools.

## Presentation checks
Run `python -m pytest -q`, then run credentialed smoke checks with
`python -m pytest -m live_api tests/live -q`.
```

- [ ] **Step 4: Run full verification**

Run:

```bash
ruff check src tests
python -m pytest -q
python -m pytest -m live_api tests/live -q
```

Expected: static checks and deterministic test suite PASS; live smoke tests PASS only when presentation credentials and billing-enabled provider access are correctly configured.

- [ ] **Step 5: Visually verify the Streamlit workflow**

Start:

```bash
python -m streamlit run src/travel_planner/ui/app.py --server.port 8501
```

Use the Browser tool to test desktop and mobile-width rendering for:

- Trip specification confirmation with `RELAXED` pace.
- Pace conflict showing actual minutes and the selected threshold.
- Budget conflict showing source records and FX snapshot.
- Budget increase branch proceeding to review without generating a new candidate route.
- Approved result rendering verified stops and route map without overlap or clipped text.

- [ ] **Step 6: Commit the deliverable**

```bash
git add README.md .env.example tests/live tests/workflow/test_demo_acceptance.py
git commit -m "docs: document live demo setup and acceptance checks"
```

## Implementation Checkpoints

At the end of Task 5, verify the system can deterministically classify pace and budget conflicts without any API or LLM calls. At the end of Task 8, verify the core human decision paths work entirely with fakes. At the end of Task 11, verify real provider credentials and UI behavior immediately before presentation.

## Requirements Coverage

| Confirmed Requirement | Implemented By |
| --- | --- |
| Five visible Agent roles with deterministic verification boundaries | Tasks 7, 8, 10 |
| Superpowers-guided gates, structured feedback, completion verification | Tasks 5, 8, 9 |
| Real Places and Routes APIs | Tasks 3, 6, 11 |
| Real exchange-rate snapshot and TWD conversion evidence | Tasks 3, 4, 6, 10 |
| User-confirmed official pricing for missing exact values | Tasks 2, 4, 10 |
| Four pace modes; "不想太累" defaults to 90-minute relaxed rule | Tasks 2, 5, 10 |
| Pace overrun decision and replanning | Tasks 5, 8, 10, 11 |
| Budget overrun branches including keep-plan/increase-budget | Tasks 4, 5, 8, 10, 11 |
| Restaurant added to final route and revalidated for pace | Tasks 6, 8 |
| Dedicated API provenance file for later maintenance | Task 3 |
| Langfuse trace and non-blocking telemetry failure | Task 9 |
| Streamlit evidence/map demo and verification | Tasks 10, 11 |
