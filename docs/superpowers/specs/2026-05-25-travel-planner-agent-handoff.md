# Multi-Agent Travel Planner Agent Handoff

Date: 2026-05-25

This document is for the next agent taking over the project.

## 1. Project State

- Repository root: `/Users/mumi/文件/多 Agent 旅遊規劃系統`
- Active branch: `main`
- Current committed HEAD: `3aad0b4` `test: verify travel planner preflight UI workflow`
- There are important **uncommitted** local changes on top of `main`.

Current working tree status:

```text
 M src/travel_planner/agents/runner.py
 M src/travel_planner/domain/models.py
 M src/travel_planner/integrations/google_routes.py
 M src/travel_planner/ui/app.py
 M src/travel_planner/workflow/orchestrator.py
 M tests/agents/test_runner.py
 M tests/integrations/test_google_routes.py
 M tests/ui/test_view_models.py
 M tests/workflow/test_demo_acceptance.py
 M tests/workflow/test_orchestrator.py
```

These local changes are tested and currently passing, but have **not** been committed yet.

## 2. Verified Current Test State

The latest local state was verified with:

```bash
PYTHONPATH=src .worktrees/feature-travel-planner-mvp/.venv/bin/python -m pytest -q
PYTHONPATH=src .worktrees/feature-travel-planner-mvp/.venv/bin/python -m ruff check src tests README.md
```

Latest result:

- `pytest -q` -> `99 passed, 5 skipped`
- `ruff check` -> passed

## 3. Runtime / Launch Instructions

Important environment note:

- The reusable virtualenv lives at:
  `/Users/mumi/文件/多 Agent 旅遊規劃系統/.worktrees/feature-travel-planner-mvp/.venv`
- To avoid old editable-install path issues, always launch from repo root with:
  - `PYTHONPATH=src`

Recommended launch command:

```bash
cd "/Users/mumi/文件/多 Agent 旅遊規劃系統"
set -a
source .worktrees/feature-travel-planner-mvp/.env
set +a
export LANGFUSE_HOST="https://jp.cloud.langfuse.com"
PYTHONPATH=src .worktrees/feature-travel-planner-mvp/.venv/bin/python -m streamlit run src/travel_planner/ui/app.py --server.port 8502 --server.headless true
```

The app has been running successfully on:

- [http://localhost:8502](http://localhost:8502)

## 4. What Has Been Added Since `3aad0b4`

### 4.1 Preflight UX and map workflow

Implemented in local changes:

- left form + right preview map layout
- destination preview on map
- hotel candidate preview on map
- must-visit preview on map
- manual hotel override input
- budget/day controls as synced slider + number input

Main file:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/ui/app.py`

### 4.2 Trip planning correctness fixes

Implemented in local changes:

- `must_visit` is now treated as locked input to daily planning
- itinerary agent receives:
  - `must_visit`
  - `remaining_slots`
  - `route_mode`
  - `walking_preference`
- planner no longer blindly replaces a preview-grounded must-visit with a different-city variant

Main files:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/agents/runner.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/workflow/orchestrator.py`

### 4.3 OpenAI structured runner resilience

Implemented in local changes:

- Azure structured LLM call now handles SDK parameter compatibility
- retries after `LengthFinishReasonError`
- uses smaller JSON-only retry prompt on retry

Main file:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/agents/runner.py`

### 4.4 Route validation fallback

Implemented in local changes:

- `TRANSIT` route mode remains primary
- when `TRANSIT` returns empty routes, `AUTO` falls back to `DRIVE`
- route evidence marks:
  - `Google Routes API`
  - or `Google Routes API (drive fallback)`

Main file:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/integrations/google_routes.py`

### 4.5 Workflow visibility fix

Implemented in local changes:

- UI no longer labels all workflow stages as `完成`
- manual review now shows the actual blocking stage/reason

Main file:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/ui/app.py`

### 4.6 New route preference controls

Implemented in local changes:

- added `RouteMode`
  - `TRANSIT`
  - `DRIVE`
  - `AUTO`
- added `WalkingPreference`
  - `NORMAL`
  - `PREFER_WALKING`
  - `SHORT_WALK_ONLY`

Main files:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/domain/models.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/ui/app.py`

### 4.7 Multi-day navigation

Implemented in local changes:

- when a day reaches `DAY_APPROVED` and there are remaining trip days, UI now shows:
  - `規劃第 N 天`

Main file:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/ui/app.py`

## 5. Current Open Work / Known Gaps

These items are not fully finished, even though the current code passes tests:

### 5.1 Sticky map needs browser-level verification

The map column was made sticky via CSS anchored in `app.py`, but it has not yet been visually verified across enough scroll cases in the in-app browser.

What to check:

- desktop viewport
- mobile/narrow viewport behavior
- whether only the intended right-side column becomes sticky
- whether sticky behavior breaks after the app transitions from preflight to approved itinerary view

### 5.2 Route mode UI is wired, but walking preference is only prompt-level today

Current state:

- `route_mode` affects real routing
- `walking_preference` is stored and passed to the itinerary prompt

Not yet done:

- no deterministic validator behavior change based on `walking_preference`
- no explicit filtering logic yet such as:
  - prefer shorter walking links
  - reject walking distance over stricter thresholds

### 5.3 Final UI wording for drive fallback warning

`source_provider` is already marked as `Google Routes API (drive fallback)`, but the UI does not yet surface a strong user-facing warning like:

> 此段無可取得的大眾運輸路線，已改用駕車估算。

This should likely be added near route evidence and/or warnings.

### 5.4 Day-to-day state is basic

Current multi-day flow supports advancing to the next day, but does not yet provide:

- a summary of completed days
- visited-place carryover beyond current workflow assumptions
- a strong end-of-trip summary view

The button-level flow exists; the broader trip progress UX still needs refinement.

## 6. Files Most Relevant for Continued Work

Primary implementation files:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/ui/app.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/ui/map_component.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/integrations/google_routes.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/agents/runner.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/workflow/orchestrator.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/src/travel_planner/domain/models.py`

Primary tests:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/tests/ui/test_view_models.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/tests/integrations/test_google_routes.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/tests/agents/test_runner.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/tests/workflow/test_orchestrator.py`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/tests/workflow/test_demo_acceptance.py`

Specs and plans already written:

- `/Users/mumi/文件/多 Agent 旅遊規劃系統/docs/superpowers/specs/2026-05-24-multi-agent-travel-planner-superpowers-design.md`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/docs/superpowers/specs/2026-05-25-travel-planner-ui-and-hotel-selection-design.md`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/docs/superpowers/plans/2026-05-24-multi-agent-travel-planner-mvp.md`
- `/Users/mumi/文件/多 Agent 旅遊規劃系統/docs/superpowers/plans/2026-05-25-travel-planner-ui-and-hotel-selection-implementation.md`

## 7. Recommended Next Actions for the Next Agent

Recommended order:

1. Commit the current tested local changes as one coherent checkpoint.
2. Open the app in the in-app browser and verify:
   - sticky map behavior
   - route mode selectbox
   - walking preference selectbox
   - next-day button after successful approval
3. Add explicit UI warning for `drive fallback`.
4. Decide whether `walking_preference` should affect:
   - only prompts
   - or deterministic pace/route validation too
5. Improve end-of-trip multi-day UX if the product demo requires it.

## 8. Safe Commit Boundary

The current uncommitted changes already form a coherent checkpoint:

- route mode support
- walking preference model + UI
- sticky map CSS
- next-day button
- prior fixes for:
  - must-visit lock-in
  - route fallback
  - LLM retry compatibility
  - manual review explanation

If the next agent wants to continue cleanly, the first action should be:

```bash
git add src tests
git commit -m "feat: improve planner controls and day progression"
```

That commit message is only a suggestion; the important point is to checkpoint before further UI work.
