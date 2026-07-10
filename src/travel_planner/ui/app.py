# -*- coding: utf-8 -*-
import pathlib as _pathlib
import sys as _sys

# Ensure the src directory is in sys.path regardless of how Streamlit launches this script.
# Streamlit may pre-load a stale version of travel_planner (e.g. from a git worktree's
# cached bytecode) into sys.modules before app.py runs. We evict any stale copy here so
# that all travel_planner imports below pick up the current source files from _src_dir.
_src_dir = str(_pathlib.Path(__file__).resolve().parent.parent.parent)
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

# Evict stale travel_planner entries so fresh imports use _src_dir above.
# Sentinel rules — extend this block whenever a new symbol or method is added:
#   • domain.models  : _REQUIRED_SYMBOLS + MacroCitySegment.hotel_descriptions field
#                      SlotType must have LUNCH/DINNER (added with meal-type fix)
#                      RouteEvidence must have total_distance_km (dual-distance fix)
#   • agents.runner  : LiveAgentSuite.set_metrics_callback (added with live-metrics feature)
#                      AzureStructuredLlm._METRICS_CB_VERSION >= 2 (per-agent 4-arg callback)
#
# IMPORTANT: when domain model fields are added, stale Pydantic *instances* already stored
# in st.session_state will also be missing those fields. The sentinel therefore purges the
# domain-object session keys (workflow / workflow_result / approved_days) whenever it detects
# a schema mismatch, so the user gets a clean slate rather than a crash.
_REQUIRED_SYMBOLS = ("DestinationLeg", "SlotType", "TimeSlot")
_tp_mods = _sys.modules.get("travel_planner.domain.models")
_runner_mod = _sys.modules.get("travel_planner.agents.runner")
if _tp_mods is not None or _runner_mod is not None:
    _seg_cls   = getattr(_tp_mods,    "MacroCitySegment",  None) if _tp_mods    else None
    _slot_cls  = getattr(_tp_mods,    "SlotType",          None) if _tp_mods    else None
    _route_cls = getattr(_tp_mods,    "RouteEvidence",     None) if _tp_mods    else None
    _las_cls   = getattr(_runner_mod, "LiveAgentSuite",    None) if _runner_mod else None
    _azure_cls = getattr(_runner_mod, "AzureStructuredLlm",None) if _runner_mod else None
    _is_stale = (
        (_tp_mods is not None and any(not hasattr(_tp_mods, _sym) for _sym in _REQUIRED_SYMBOLS))
        or (_seg_cls   is not None and "hotel_descriptions" not in getattr(_seg_cls,   "model_fields", {}))
        or (_slot_cls  is not None and not hasattr(_slot_cls, "LUNCH"))
        or (_route_cls is not None and "total_distance_km"  not in getattr(_route_cls, "model_fields", {}))
        or (_las_cls   is not None and not hasattr(_las_cls,   "set_metrics_callback"))
        or (_azure_cls is not None and getattr(_azure_cls, "_METRICS_CB_VERSION", 0) < 2)
    )
    if _is_stale:
        for _k in [k for k in _sys.modules if k.startswith("travel_planner")]:
            del _sys.modules[_k]
        # Purge session-state keys that hold domain-model instances created under
        # the old schema. Chat history, settings, and UI state are intentionally
        # preserved so the user does not lose their entire session.
        _st_mod = _sys.modules.get("streamlit")
        if _st_mod is not None:
            for _skey in ("workflow", "workflow_result", "approved_days"):
                _st_mod.session_state.pop(_skey, None)
        del _st_mod
    del _seg_cls, _slot_cls, _route_cls, _las_cls, _azure_cls, _is_stale
del _tp_mods, _runner_mod, _REQUIRED_SYMBOLS, _pathlib

from decimal import Decimal, InvalidOperation

import httpx
from pydantic import ValidationError

try:  # pragma: no cover - exercised only when streamlit is installed
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover - import fallback for unit tests
    st = None

from travel_planner.agents.runner import IntentParseResult, LiveAgentSuite
from travel_planner.config import Settings
from travel_planner.domain.models import (
    ConflictEvent,
    ConflictType,
    DayPlanState,
    DestinationLeg,
    ExchangeRateSnapshot,
    PlaceStop,
    PriceRecord,
    PriceStatus,
    RouteMode,
    SlotType,
    TripSpec,
    UserChoice,
    UserDecision,
    WalkingPreference,
)
from travel_planner.domain.pace import PaceLevel, get_pace_profile
from travel_planner.integrations.api_registry import PROVIDERS
from travel_planner.integrations.exchange_rates import ExchangeRateClient
from travel_planner.integrations.google_places import GooglePlacesClient, GroundingNotFound
from travel_planner.integrations.google_routes import GoogleRoutesClient, RouteResult
from travel_planner.observability.tracing import LangfuseTracer, NoOpTracer
from travel_planner.ui.form_state import RangeFieldSpec, coerce_range_value
from travel_planner.ui.map_component import render_preview_map, render_verified_route_map
from travel_planner.ui.pdf_generator import generate_itinerary_pdf
from travel_planner.validation.budget import evaluate_budget
from travel_planner.workflow.orchestrator import TravelWorkflow, WorkflowResult, WorkflowStatus

PACE_LABELS = {
    PaceLevel.VERY_RELAXED: "非常悠閒",
    PaceLevel.RELAXED: "悠閒",
    PaceLevel.STANDARD: "一般",
    PaceLevel.INTENSIVE: "密集",
}

PACE_DESCRIPTIONS = {
    PaceLevel.VERY_RELAXED: "每天 1-2 個重點，必要移動上限 75 分鐘。",
    PaceLevel.RELAXED: "每天最多 2 個重點，必要移動上限 90 分鐘。",
    PaceLevel.STANDARD: "每天最多 3 個重點，必要移動上限 120 分鐘。",
    PaceLevel.INTENSIVE: "每天最多 4 個重點，必要移動上限 180 分鐘。",
}

SESSION_WORKFLOW = "workflow"
SESSION_RESULT = "workflow_result"
SESSION_SETTINGS = "settings"
SESSION_ERROR = "settings_error"
SESSION_DESTINATION_STOP = "destination_stop"
SESSION_MUST_VISIT_STOPS = "must_visit_stops"
SESSION_MUST_VISIT_ERRORS = "must_visit_errors"
SESSION_APPROVED_DAYS = "approved_days"
# Multi-city city rows
SESSION_NUM_EXTRA_CITIES = "num_extra_cities"
# Macro-phase hotel selection
SESSION_MACRO_HOTELS = "macro_hotels"   # dict[city, PlaceStop]
SESSION_MACRO_STEP = "macro_step"       # int: which city's hotel we're picking
# Extra-city destination stops for map preview in the left dashboard
SESSION_EXTRA_DEST_STOPS = "extra_dest_stops"
# Phase 0 conversational onboarding
SESSION_CHAT_HISTORY = "chat_history"       # list[dict] with role/content
SESSION_COLLECTED_PARAMS = "collected_params"  # dict of extracted TripSpec fields
# Phase 1/2 decision injection tracking
SESSION_DECISION_KEY = "_decision_injected_key"  # str key to avoid double-injecting chat prompts
SESSION_USER_CONSTRAINTS = "user_constraints"   # list[str] — accumulated re-plan feedback
# Macro two-step flow: True once user confirms the plan overview (before hotel selection)
SESSION_MACRO_PLAN_APPROVED = "macro_plan_approved"
# Per-agent live metrics: {agent_name: {status, in_tokens, out_tokens, time_s}}
SESSION_METRICS = "workflow_metrics"
# Sidebar updater closures — set each render cycle in main() so callbacks can refresh the UI
SESSION_SIDEBAR_UPDATERS = "_sidebar_updaters"

# Canonical list of all agent names (matches LiveAgentSuite English names in runner.py)
_AGENT_NAMES = ["Intent Parser", "Macro Planner", "Itinerary Agent", "Food Agent", "Reviewer Agent"]

# Maps substrings found in _emit() progress messages to English agent names
# (orchestrator uses Chinese UX labels; this bridges them to the metrics dashboard)
_EMIT_TO_AGENT: dict[str, str] = {
    "行程 Agent": "Itinerary Agent",
    "美食 Agent": "Food Agent",
    "審查 Agent": "Reviewer Agent",
}


def _fresh_metrics() -> dict:
    """Return a clean per-agent metrics dict for a new planning session."""
    return {
        name: {"status": "💤 Idle", "in_tokens": 0, "out_tokens": 0, "time_s": 0.0}
        for name in _AGENT_NAMES
    }


def _render_metrics_table(metrics: dict) -> str:
    """Render per-agent metrics as a compact markdown table."""
    rows = [
        "| Agent | St | Time | In | Out |",
        "|---|:---:|---:|---:|---:|",
    ]
    for name, data in metrics.items():
        rows.append(
            f"| {name} | {data['status']} | {data['time_s']:.1f}s"
            f" | {data['in_tokens']:,} | {data['out_tokens']:,} |"
        )
    return "\n".join(rows)

DAYS_SPEC = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)
TOTAL_BUDGET_SPEC = RangeFieldSpec(label="總預算", minimum=1000, maximum=300000, step=1000, unit="NTD")

# Placeholder used until user selects a real hotel during macro phase
_PLACEHOLDER_HOTEL = PlaceStop(name="__待確認__", place_id="__placeholder__")

_WELCOME_MESSAGE = (
    "你好！我是你的 AI travel planner 🌏\n\n"
    "不需要繁瑣的設定，請**直接告訴我**你想去哪裡、去幾天？\n\n"
    "**例如：**\n"
    "> 「下個月想和伴侶去大阪玩 5 天，預算 3 萬台幣，"
    "想吃道頓堀美食和去環球影城！」\n\n"
    "我會幫你規劃完整的每日行程，包含景點安排、交通路線與住宿推薦 ✈️"
)

ROUTE_MODE_LABELS = {
    RouteMode.TRANSIT: "大眾運輸",
    RouteMode.DRIVE: "駕車估算",
    RouteMode.AUTO: "自動（先大眾運輸，失敗再駕車）",
}

WALKING_PREFERENCE_LABELS = {
    WalkingPreference.NORMAL: "一般",
    WalkingPreference.PREFER_WALKING: "偏好步行",
    WalkingPreference.SHORT_WALK_ONLY: "僅短距離步行",
}


class LivePriceCollector:
    def collect(self, day_state: DayPlanState, route_result: RouteResult) -> list[PriceRecord]:
        prices: list[PriceRecord] = []
        if route_result.transit_fare is not None:
            prices.append(route_result.transit_fare)
        return prices


def format_pace_conflict(observed_minutes: int, limit_minutes: int) -> str:
    pace_name = _pace_label_for_limit(limit_minutes)
    return f"此日必要移動 {observed_minutes} 分鐘，超過{pace_name}模式上限 {limit_minutes} 分鐘。"


def format_price_source(original_price: str, provider: str) -> str:
    return f"{original_price} | 資料來源：{provider}"


def _workflow_step_statuses(result: WorkflowResult) -> dict[str, str]:
    statuses = {
        "行程 Agent": "待執行",
        "Places 驗證": "待執行",
        "Routes 驗證": "待執行",
        "美食 Agent": "待執行",
        "Budget Gate": "待執行",
        "檢查 Agent": "待執行",
    }
    warnings = set(result.day_state.warnings)

    if result.day_state.status.value != "DRAFT" or result.day_state.places:
        statuses["行程 Agent"] = "完成"

    if result.day_state.places:
        statuses["Places 驗證"] = "完成"
    elif "GROUNDING_FAILED" in warnings and result.status is WorkflowStatus.NEEDS_MANUAL_REVIEW:
        statuses["Places 驗證"] = "失敗"

    if "ROUTE_UNAVAILABLE" in warnings and result.status is WorkflowStatus.NEEDS_MANUAL_REVIEW:
        statuses["Routes 驗證"] = "失敗"
    elif result.day_state.route is not None:
        statuses["Routes 驗證"] = "完成"
    elif result.status is WorkflowStatus.AWAITING_PACE_DECISION:
        statuses["Routes 驗證"] = "需決策"

    if result.day_state.meals:
        statuses["美食 Agent"] = "完成"

    if result.status in {
        WorkflowStatus.AWAITING_BUDGET_DECISION,
        WorkflowStatus.AWAITING_PRICE_DECISION,
    }:
        statuses["Budget Gate"] = "需決策"
    elif result.status is WorkflowStatus.DAY_APPROVED:
        statuses["Budget Gate"] = "完成"

    if result.day_state.quality_score is not None or result.status is WorkflowStatus.DAY_APPROVED:
        statuses["檢查 Agent"] = "完成"

    return statuses


def _format_manual_review_reason(result: WorkflowResult) -> str:
    warnings = result.day_state.warnings if result.day_state else []
    warnings_set = set(warnings)
    # Reviewer veto: the specific rejection reason is appended to warnings as
    # "REVIEWER_VETO:<summary>" when retry budget is exhausted.  Show it last
    # so the user understands exactly why the AI rejected the plan.
    for w in reversed(warnings):
        if w.startswith("REVIEWER_VETO:"):
            veto_reason = w[len("REVIEWER_VETO:"):].strip() or "行程品質未達標準。"
            return f"審查 Agent 多次評分不足，重試上限已達：{veto_reason}"
    if "ROUTE_UNAVAILABLE" in warnings_set:
        return "路線驗證連續失敗兩次，系統無法算出完整交通路線。請調整住宿、景點組合或稍後重試。"
    if "GROUNDING_FAILED" in warnings_set:
        return "景點解析連續失敗兩次，系統找不到可用的正式地點資料。請改用更完整的景點名稱或調整目的地。"
    return "系統無法完成此日的自動驗證流程。請調整住宿、景點或稍後重試。"


def _can_plan_next_day(*, current_day: int, total_days: int) -> bool:
    return current_day < total_days


def _render_synced_range_input(
    label: str,
    *,
    spec: RangeFieldSpec,
    slider_key: str,
    input_key: str,
    default: int,
) -> int | Decimal:
    _require_streamlit()
    normalized_default = _normalize_range_state_value(default, spec)
    if slider_key not in st.session_state:
        st.session_state[slider_key] = int(normalized_default)
    if input_key not in st.session_state:
        st.session_state[input_key] = normalized_default

    st.slider(
        label,
        min_value=spec.minimum,
        max_value=spec.maximum,
        value=int(st.session_state[slider_key]),
        step=spec.step,
        key=slider_key,
        on_change=_sync_range_from_slider,
        args=(slider_key, input_key, spec),
    )
    st.number_input(
        f"{label}（輸入）",
        min_value=spec.minimum,
        max_value=spec.maximum,
        value=int(st.session_state[input_key]),
        step=spec.step,
        key=input_key,
        on_change=_sync_range_from_input,
        args=(slider_key, input_key, spec),
    )
    return coerce_range_value(st.session_state[input_key], spec)


def _normalize_range_state_value(raw: str | int | float | Decimal, spec: RangeFieldSpec) -> int:
    coerced = coerce_range_value(raw, spec)
    return int(coerced)


def _sync_range_from_slider(slider_key: str, input_key: str, spec: RangeFieldSpec) -> None:
    _require_streamlit()
    st.session_state[input_key] = _normalize_range_state_value(st.session_state[slider_key], spec)


def _sync_range_from_input(slider_key: str, input_key: str, spec: RangeFieldSpec) -> None:
    _require_streamlit()
    normalized = _normalize_range_state_value(st.session_state[input_key], spec)
    st.session_state[input_key] = normalized
    st.session_state[slider_key] = int(normalized)


def render_trip_spec_form() -> None:
    _require_streamlit()
    st.header("旅程需求")
    try:
        settings = Settings()
    except (ValidationError, ValueError, InvalidOperation) as error:
        st.session_state[SESSION_ERROR] = str(error)
        return
    except Exception as error:  # pragma: no cover - UI fallback
        st.session_state[SESSION_ERROR] = f"初始化 live workflow 失敗：{error}"
        return

    # ── City rows (main + extra with + button) ────────────────────────
    num_extra = st.session_state.get(SESSION_NUM_EXTRA_CITIES, 0)

    destination = st.text_input(
        "🏙️ 城市 1（主要目的地）",
        key="main-city-dest",
        placeholder="e.g. Osaka",
    )

    extra_leg_raw_list: list[dict] = []
    extra_destination_stops: list[PlaceStop] = []

    for i in range(num_extra):
        st.markdown(f"**🏙️ 城市 {i + 2}**")
        col_city, col_days, col_rm = st.columns([3, 1, 0.5])
        with col_city:
            extra_city = st.text_input(
                "名稱",
                key=f"extra-city-{i}-name",
                placeholder=f"e.g. {'Kyoto' if i == 0 else 'Tokyo'}",
                label_visibility="collapsed",
            )
        with col_days:
            extra_days = int(
                st.number_input(
                    "天數",
                    min_value=1,
                    max_value=10,
                    value=st.session_state.get(f"extra-city-{i}-days", 2),
                    step=1,
                    key=f"extra-city-{i}-days",
                    label_visibility="collapsed",
                )
            )
        with col_rm:
            if st.button("✕", key=f"rm-city-{i}", help=f"移除城市 {i + 2}"):
                # Shift subsequent rows down
                for j in range(i, num_extra - 1):
                    st.session_state[f"extra-city-{j}-name"] = st.session_state.get(
                        f"extra-city-{j + 1}-name", ""
                    )
                    st.session_state[f"extra-city-{j}-days"] = st.session_state.get(
                        f"extra-city-{j + 1}-days", 2
                    )
                    st.session_state[f"extra-city-{j}-must"] = st.session_state.get(
                        f"extra-city-{j + 1}-must", ""
                    )
                st.session_state[SESSION_NUM_EXTRA_CITIES] = num_extra - 1
                st.rerun()

        extra_must_raw = st.text_area(
            f"城市 {i + 2} 必去景點（選填，逗號分隔）",
            key=f"extra-city-{i}-must",
            height=60,
        )
        extra_leg_raw_list.append({
            "city": extra_city.strip(),
            "num_days": extra_days,
            "hotel_name": "",   # Hotel selected during macro phase
            "must_visit_raw": extra_must_raw,
        })
        if extra_city.strip():
            extra_dest = _preview_destination(settings, extra_city.strip())
            if extra_dest:
                extra_destination_stops.append(extra_dest)

    if num_extra < 2:
        if st.button("＋ 新增城市", key="add-city-btn"):
            st.session_state[SESSION_NUM_EXTRA_CITIES] = num_extra + 1
            st.rerun()

    st.divider()

    # ── Days + Budget ─────────────────────────────────────────────────
    days = _render_synced_range_input(
        "旅遊天數（城市 1）",
        spec=DAYS_SPEC,
        slider_key="trip-days-slider",
        input_key="trip-days-input",
        default=5,
    )
    budget_amount = _render_synced_range_input(
        "總預算",
        spec=TOTAL_BUDGET_SPEC,
        slider_key="budget-total-slider",
        input_key="budget-total-input",
        default=25000,
    )
    budget_currency = st.selectbox("預算幣別", options=["TWD"], index=0)

    # ── Transport + Interests ─────────────────────────────────────────
    route_mode = st.selectbox(
        "交通方式",
        options=list(RouteMode),
        index=2,
        format_func=lambda value: ROUTE_MODE_LABELS[value],
    )
    walking_preference = st.selectbox(
        "步行偏好",
        options=list(WalkingPreference),
        index=0,
        format_func=lambda value: WALKING_PREFERENCE_LABELS[value],
    )
    interests = st.text_input("興趣", value="anime, food")

    # ── Must-visit (main city only; extra cities handled above) ───────
    must_visit_name = st.text_area("城市 1 必去景點", value="Universal Studios Japan")
    must_visit_price = st.text_input("必去景點價格 (JPY)", value="8600")
    must_visit_price_url = st.text_input("必去景點官方價格 URL")

    pace_level = st.radio(
        "旅遊節奏",
        options=list(PaceLevel),
        format_func=lambda value: f"{PACE_LABELS[value]}: {PACE_DESCRIPTIONS[value]}",
        index=1,
    )

    must_visit_stops, must_visit_errors = _preview_must_visit_stops(settings, must_visit_name)
    destination_stop = _preview_destination(settings, destination)

    st.session_state[SESSION_DESTINATION_STOP] = destination_stop
    st.session_state[SESSION_MUST_VISIT_STOPS] = must_visit_stops
    st.session_state[SESSION_MUST_VISIT_ERRORS] = must_visit_errors
    st.session_state[SESSION_EXTRA_DEST_STOPS] = extra_destination_stops

    # Show must-visit validation errors inline (map is in the left dashboard)
    for warning in must_visit_errors:
        st.warning(warning)

    st.caption("🏨 住宿將在下一步的旅行計畫概覽中由 AI 推薦，並由您確認。")
    submitted = st.button("開始規劃")

    if not submitted:
        return

    try:
        _validate_trip_submission_inputs(
            destination=destination,
            days=int(days),
            budget_amount=str(budget_amount),
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
        )
        grounded_extra_legs = _ground_extra_legs(settings, extra_leg_raw_list)
        trip_spec = _build_trip_spec_from_preflight(
            settings=settings,
            destination=destination,
            days=int(days),
            budget_amount=str(budget_amount),
            budget_currency=budget_currency,
            interests=interests,
            pace_level=pace_level,
            route_mode=route_mode,
            walking_preference=walking_preference,
            grounded_must_visit=must_visit_stops,
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
            must_visit_price_url=must_visit_price_url,
            extra_legs=grounded_extra_legs,
        )
    except (ValidationError, ValueError, InvalidOperation) as error:
        st.session_state[SESSION_ERROR] = str(error)
        return

    workflow = _build_live_workflow(settings, trip_spec)
    st.session_state[SESSION_SETTINGS] = settings
    st.session_state[SESSION_WORKFLOW] = workflow
    # Clear any leftover macro hotel state from a previous run
    st.session_state.pop(SESSION_MACRO_HOTELS, None)
    st.session_state.pop(SESSION_MACRO_STEP, None)
    # Reset metrics for this new planning session
    st.session_state[SESSION_METRICS] = _fresh_metrics()
    # Wire metrics callback so validate_macro's propose_macro_plan LLM call is tracked
    _set_mcb = getattr(workflow.agents, "set_metrics_callback", None)
    if _set_mcb is not None:
        _set_mcb(_make_metrics_cb())
    st.session_state[SESSION_RESULT] = workflow.validate_macro()
    st.session_state.pop(SESSION_ERROR, None)
    st.rerun()


def render_running_or_evidence(workflow: TravelWorkflow, result: WorkflowResult) -> None:
    _require_streamlit()
    st.header("驗證流程")
    step_statuses = _workflow_step_statuses(result)
    labels = list(step_statuses.keys())
    st.columns(len(labels))
    for column, label in zip(st.columns(len(labels)), labels, strict=True):
        column.metric(label, step_statuses[label])

    with st.expander("資料來源與限制", expanded=False):
        for provider in PROVIDERS.values():
            st.markdown(
                f"**{provider.display_name}**  \n"
                f"用途：{provider.purpose}  \n"
                f"證據欄位：{', '.join(provider.evidence_fields)}  \n"
                f"限制：{provider.limitations}  \n"
                f"[官方文件]({provider.docs_url})"
            )

    if result.day_state.places:
        st.subheader("目前已驗證地點")
        for stop in result.day_state.places + result.day_state.meals:
            st.write(f"- {stop.name} (`{stop.place_id}`)")

    if result.day_state.route:
        route = result.day_state.route
        st.subheader("路線證據")
        left, right, extra = st.columns(3)
        left.metric("必要移動", f"{route.total_required_transfer_minutes} 分")
        right.metric("最長單段", f"{route.max_single_transfer_minutes} 分")
        extra.metric(
            "總距離",
            f"{getattr(route, 'total_distance_km', 0.0):.1f} km",
            delta=f"步行 {route.walking_distance_km:.1f} km",
            delta_color="off",
        )
        if "drive fallback" in route.source_provider:
            st.warning("此段無可取得的大眾運輸路線，已改用駕車估算。路線時間僅供參考，實際通勤時間請另行確認。")


def render_decision_gate(workflow: TravelWorkflow, result: WorkflowResult) -> None:
    _require_streamlit()
    conflict = result.conflict
    if conflict is None:
        return

    st.header("需要使用者決策")
    st.warning(f"衝突類型：{conflict.conflict_type.value}")

    if conflict.conflict_type is ConflictType.PACE_EXCEEDED:
        if "walking_distance_short_walk_only" in conflict.reasons:
            observed_km = result.day_state.route.walking_distance_km if result.day_state.route else 0
            limit_km = workflow.trip_spec.pace.walking_distance_warning_km * 0.5
            st.write(
                f"此日步行距離 {observed_km:.1f} km，超過「僅短距離步行」模式上限 {limit_km:.1f} km。"
            )
        else:
            observed = int(result.day_state.route.total_required_transfer_minutes) if result.day_state.route else 0
            limit = workflow.trip_spec.pace.max_required_transfer_minutes_per_day
            st.write(format_pace_conflict(observed, limit))
            bottleneck = _extract_bottleneck(conflict)
            if bottleneck:
                st.info(f"🔍 **耗時瓶頸**：{bottleneck.replace('**', '')}")
        if st.button("維持目前節奏並重新規劃", key="keep-pace"):
            _resume_workflow(workflow, UserDecision(choice=UserChoice.KEEP_PACE_REPLAN))
        if st.button("接受這天較累", key="accept-pace"):
            _resume_workflow(workflow, UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING))
        if st.button("提高為一般模式", key="raise-pace"):
            _resume_workflow(
                workflow,
                UserDecision(
                    choice=UserChoice.INCREASE_DAY_PACE,
                    new_pace=get_pace_profile(PaceLevel.STANDARD),
                ),
            )
        return

    if conflict.conflict_type is ConflictType.PRICE_MISSING:
        st.write("仍有未確認價格。可以補價，或接受價格未確認警告後繼續。")
        if st.button("接受價格未確認警告", key="accept-cost-warning"):
            _resume_workflow(workflow, UserDecision(choice=UserChoice.ACCEPT_COST_WARNING))
        return

    outcome = evaluate_budget(
        workflow.trip_spec.prices + result.day_state.prices,
        workflow.trip_spec.fx_snapshot,
        workflow.trip_spec.budget_override_history[-1]
        if workflow.trip_spec.budget_override_history
        else workflow.trip_spec.budget_amount,
    )
    st.write(
        f"已確認費用 {workflow.trip_spec.budget_currency} {outcome.confirmed_total}，"
        f"區間上限 {workflow.trip_spec.budget_currency} {outcome.maximum_total}。"
    )
    new_budget = st.number_input(
        "若要加預算，請輸入新的總預算上限",
        min_value=float(workflow.trip_spec.budget_amount),
        value=float(
            workflow.trip_spec.budget_override_history[-1]
            if workflow.trip_spec.budget_override_history
            else workflow.trip_spec.budget_amount
        ),
        step=1000.0,
    )
    if st.button("增加預算並保留行程", key="increase-budget"):
        _resume_workflow(
            workflow,
            UserDecision(
                choice=UserChoice.INCREASE_BUDGET_KEEP_PLAN,
                new_budget_limit=Decimal(str(new_budget)),
            ),
        )
    if st.button("保留必去項目並壓低其他支出", key="reduce-cost"):
        _resume_workflow(workflow, UserDecision(choice=UserChoice.KEEP_LOCKED_REDUCE_COST))
    if st.button("維持預算並允許換景點", key="replace-items"):
        _resume_workflow(workflow, UserDecision(choice=UserChoice.REPLACE_ITEMS_KEEP_BUDGET))
    if st.button("接受可能超支警告", key="accept-budget-warning"):
        _resume_workflow(workflow, UserDecision(choice=UserChoice.ACCEPT_COST_WARNING))


def render_macro_decision(
    workflow: TravelWorkflow, result: WorkflowResult, settings: Settings
) -> None:
    """Phase 1 UI: city overview cards + guided hotel selection + feasibility check."""
    _require_streamlit()
    macro = workflow.macro_plan
    conflict = result.conflict

    # ── Session state init ────────────────────────────────────────────────
    if SESSION_MACRO_HOTELS not in st.session_state:
        st.session_state[SESSION_MACRO_HOTELS] = {}
    if SESSION_MACRO_STEP not in st.session_state:
        st.session_state[SESSION_MACRO_STEP] = 0

    macro_hotels: dict = st.session_state[SESSION_MACRO_HOTELS]
    cities = _get_cities_for_hotel_selection(workflow)
    current_step: int = st.session_state[SESSION_MACRO_STEP]
    all_confirmed = len(macro_hotels) >= len(cities)

    st.header("Phase 1 — 旅行計畫概覽")
    st.caption("AI 已根據您的需求規劃初步行程。請確認各城市住宿後開始細部規劃。")

    # ── City overview cards ───────────────────────────────────────────────
    if macro and macro.city_segments:
        n = len(macro.city_segments)
        cols = st.columns(min(n, 3))
        for idx, seg in enumerate(macro.city_segments):
            col = cols[idx % min(n, 3)]
            with col:
                confirmed_hotel = macro_hotels.get(seg.city)
                _render_city_overview_card(seg, confirmed_hotel)
    elif macro and macro.day_slots:
        # Fallback: LLM call failed, show bare skeleton
        st.subheader("行程骨架")
        slot_cols = st.columns(min(len(macro.day_slots), 5))
        for idx, slot in enumerate(macro.day_slots):
            label = "🚆 移動日" if slot.is_travel_day else slot.city
            slot_cols[idx % len(slot_cols)].metric(f"第 {slot.day} 天", label)

    # ── Feasibility warnings ──────────────────────────────────────────────
    if conflict and conflict.reasons:
        st.divider()
        st.subheader("⚠️ 發現可行性問題")
        for reason in conflict.reasons:
            st.warning(reason)

    st.divider()

    # ── Hotel selection (guided, step-by-step) ────────────────────────────
    if not all_confirmed:
        if current_step < len(cities):
            current_city = cities[current_step]
            seg = next(
                (s for s in (macro.city_segments or []) if s.city == current_city),
                None,
            )
            _render_hotel_selection_step(
                settings=settings,
                city=current_city,
                seg=seg,
                step=current_step,
                total=len(cities),
            )
    else:
        # ── All hotels confirmed → show summary + final confirm ───────────
        st.success("✅ 所有城市住宿已確認！")
        for city, hotel in macro_hotels.items():
            st.write(f"&nbsp;&nbsp;🏨 **{city}**：{hotel.name}")

        st.markdown("")
        proceed_label = (
            "⚠️ 瞭解問題，確認並開始細部規劃"
            if (conflict and conflict.reasons)
            else "✅ 確認計畫，開始細部規劃"
        )
        col_go, col_back = st.columns([2, 1])
        with col_go:
            if st.button(proceed_label, key="macro-proceed", use_container_width=True):
                _apply_macro_hotels_to_workflow(workflow, macro_hotels)
                _resume_workflow(workflow, UserDecision(choice=UserChoice.PROCEED_WITH_WARNINGS))
        with col_back:
            if st.button("← 返回修改", key="macro-back", use_container_width=True):
                for key in [
                    SESSION_WORKFLOW, SESSION_RESULT, SESSION_SETTINGS,
                    SESSION_MACRO_HOTELS, SESSION_MACRO_STEP,
                ]:
                    st.session_state.pop(key, None)
                st.rerun()


def render_duplicate_decision(workflow: TravelWorkflow, result: WorkflowResult) -> None:
    """Phase 2 UI: ask the user whether to allow a duplicate place."""
    _require_streamlit()
    conflict = result.conflict
    if conflict is None:
        return

    duplicate_name = conflict.evidence.get("duplicate_place_name", "（未知地點）")
    day = result.day_state.day

    st.header(f"第 {day} 天 — 重複地點提示")
    st.info(
        f"「**{duplicate_name}**」在之前的行程中已安排過。\n\n"
        f"請選擇：是否要再次造訪？"
    )
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button(f"✅ 是，再次造訪 {duplicate_name}", key="dup-allow"):
            _resume_workflow(workflow, UserDecision(choice=UserChoice.ALLOW_REVISIT))
    with col_no:
        if st.button("❌ 否，重新規劃此日行程", key="dup-reject"):
            _resume_workflow(workflow, UserDecision(choice=UserChoice.REJECT_DUPLICATE))


def render_approved_itinerary(settings: Settings, workflow: TravelWorkflow, result: WorkflowResult) -> None:
    _require_streamlit()
    st.header("已核准日程")
    route = result.day_state.route
    fx = workflow.trip_spec.fx_snapshot
    if route is not None:
        left, center, right = st.columns(3)
        left.metric("必要移動", f"{route.total_required_transfer_minutes} 分")
        center.metric("最長單段", f"{route.max_single_transfer_minutes} 分")
        right.metric(
            "總距離",
            f"{getattr(route, 'total_distance_km', 0.0):.1f} km",
            delta=f"步行 {route.walking_distance_km:.1f} km",
            delta_color="off",
        )
        st.caption(
            f"交通方式：{ROUTE_MODE_LABELS[workflow.trip_spec.route_mode]} | "
            f"步行偏好：{WALKING_PREFERENCE_LABELS[workflow.trip_spec.walking_preference]}"
        )
        if "drive fallback" in route.source_provider:
            st.warning("此段無可取得的大眾運輸路線，已改用駕車估算。路線時間僅供參考，實際通勤時間請另行確認。")

    # Global memory: remaining budget
    mem = workflow.global_memory
    if mem.remaining_budget >= 0 and workflow.trip_spec.fx_snapshot is not None:
        st.metric(
            f"剩餘預算（{workflow.trip_spec.budget_currency}）",
            f"{mem.remaining_budget:,.0f}",
        )

    st.subheader("費用證據")
    for price in workflow.trip_spec.prices + result.day_state.prices:
        st.write(format_price_source(_format_original_price(price), price.source_provider))
        if fx is not None and price.status is not PriceStatus.MISSING_PRICE:
            st.caption(
                f"換算約 {workflow.trip_spec.budget_currency} {_format_converted_price(price, fx)} "
                f"(匯率時間 {fx.retrieved_at.isoformat()})"
            )

    if result.day_state.warnings:
        st.subheader("警告")
        for warning in result.day_state.warnings:
            st.write(f"- {warning}")

    if result.day_state.quality_score is not None:
        st.metric("品質評分", f"{result.day_state.quality_score} / 5")

    # Route map is now rendered in the left dashboard column.
    if _can_plan_next_day(current_day=result.day_state.day, total_days=workflow.trip_spec.total_days):
        next_day = result.day_state.day + 1
        if st.button(f"規劃第 {next_day} 天", key=f"plan-day-{next_day}"):
            approved = st.session_state.get(SESSION_APPROVED_DAYS, [])
            approved.append(result.day_state)
            st.session_state[SESSION_APPROVED_DAYS] = approved
            with st.status("⚙️ 多 Agent 團隊正在為您規劃中…", expanded=True) as _status:
                _pcb, _mcb = _make_planning_callbacks(_status)
                st.session_state[SESSION_RESULT] = workflow.start_day(
                    next_day, progress_callback=_pcb, metrics_callback=_mcb
                )
            st.rerun()
    else:
        _render_trip_summary(workflow)


def main() -> None:
    _require_streamlit()
    st.set_page_config(page_title="Multi-Agent Travel Planner", layout="wide")
    st.title("Multi-Agent Travel Planner")

    # ── Sticky left-column CSS ────────────────────────────────────────────
    st.markdown(
        """
        <style>
        /* Pin the left dashboard column while the right chat column scrolls */
        div[data-testid="stHorizontalBlock"]
            > div[data-testid="stColumn"]:first-of-type {
            position: -webkit-sticky;
            position: sticky;
            top: 3rem;
            align-self: flex-start;
            max-height: calc(100vh - 3rem);
            overflow-y: auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Session metrics init (persists across reruns within a session) ─────────
    if SESSION_METRICS not in st.session_state:
        st.session_state[SESSION_METRICS] = _fresh_metrics()

    # ── Live metrics sidebar ──────────────────────────────────────────────────
    # Two st.empty() placeholders are created fresh each render cycle so that
    # callbacks firing WITHIN the same cycle (via st.status streaming) can update
    # them in real time. The updater closures are stored in session_state so that
    # _resume_workflow() / _make_planning_callbacks() can access them without
    # needing a direct reference to the placeholder objects.
    _metrics = st.session_state[SESSION_METRICS]
    with st.sidebar:
        st.header("📊 Agent 即時狀態")
        _ph_table = st.empty()
        _ph_current = st.empty()
        # Render current accumulated values
        _ph_table.markdown(_render_metrics_table(_metrics))
        _ph_current.caption("💤 閒置中")

        # Store updater closures for this render cycle
        def _upd_redraw(m: dict) -> None:
            _ph_table.markdown(_render_metrics_table(m))

        def _upd_current(msg: str) -> None:
            _ph_current.caption(f"▶ {msg.rstrip('…')[:40]}")

        st.session_state[SESSION_SIDEBAR_UPDATERS] = {
            "redraw": _upd_redraw,
            "current": _upd_current,
        }

    error = st.session_state.get(SESSION_ERROR)
    if error:
        st.error(error)

    workflow = st.session_state.get(SESSION_WORKFLOW)
    result = st.session_state.get(SESSION_RESULT)
    settings = st.session_state.get(SESSION_SETTINGS)

    dash_col, chat_col = st.columns([6, 4])

    with dash_col:
        _render_left_dashboard(workflow, result, settings)

    with chat_col:
        _render_right_interactions(workflow, result, settings)


# ── Left Dashboard ────────────────────────────────────────────────────────────

def _render_left_dashboard(
    workflow: "TravelWorkflow | None",
    result: "WorkflowResult | None",
    settings: "Settings | None",
) -> None:
    """Left 6-column panel: live map + progressive itinerary dashboard."""
    _require_streamlit()

    # ── Map ───────────────────────────────────────────────────────────────
    # Compute approved days early — shared by both the map layer selector and
    # the itinerary dashboard below so we don't query session_state twice.
    approved_days = list(st.session_state.get(SESSION_APPROVED_DAYS, []))
    if (
        result is not None
        and result.status is WorkflowStatus.DAY_APPROVED
        and (not approved_days or approved_days[-1].day != result.day_state.day)
    ):
        approved_days = approved_days + [result.day_state]

    if settings:
        api_key = settings.google_maps_api_key.get_secret_value()
        show_route = (
            result is not None
            and result.status is WorkflowStatus.DAY_APPROVED
            and result.day_state.route is not None
            and workflow is not None
        )

        # ── Day-layer selector ────────────────────────────────────────────
        # Shows compact radio pills whenever 2+ days have route data, letting
        # the user flip between each day's verified route on the map.
        days_with_route = [ds for ds in approved_days if ds.route is not None]
        _map_view = None  # None → live route / preview
        if len(days_with_route) >= 2 or (len(days_with_route) == 1 and show_route):
            _day_opts = [f"第{ds.day}天" for ds in days_with_route]
            if show_route:
                _day_opts.append("即時")
            _default_idx = len(_day_opts) - 1  # default = rightmost (latest/live)
            _map_view = st.radio(
                "🗺️",
                _day_opts,
                index=_default_idx,
                horizontal=True,
                label_visibility="collapsed",
                key="map_day_selector",
            )

        # ── Render the selected map ───────────────────────────────────────
        if _map_view is not None and _map_view.startswith("第"):
            # Historical day chosen — look up its route
            _day_num = int(_map_view.replace("第", "").replace("天", ""))
            _hist_ds = next((ds for ds in days_with_route if ds.day == _day_num), None)
            if _hist_ds and workflow:
                _hist_hotel = _hist_ds.leg_hotel or workflow.trip_spec.hotel
                render_verified_route_map(
                    api_key,
                    [_hist_hotel] + _hist_ds.places + _hist_ds.meals,
                    _hist_ds.route.encoded_polyline,
                    _hist_ds.route.encoded_polyline_segments,
                )
                if "drive fallback" in _hist_ds.route.source_provider:
                    st.warning(
                        "此段無可取得的大眾運輸路線，已改用駕車估算。"
                        "路線時間僅供參考，實際通勤時間請另行確認。"
                    )
            else:
                st.caption(f"第{_day_num}天無路線資料。")
        elif show_route:
            # Live route for the current planning day
            day_hotel = result.day_state.leg_hotel or workflow.trip_spec.hotel
            render_verified_route_map(
                api_key,
                [day_hotel] + result.day_state.places + result.day_state.meals,
                result.day_state.route.encoded_polyline,
                result.day_state.route.encoded_polyline_segments,
            )
            if "drive fallback" in result.day_state.route.source_provider:
                st.warning(
                    "此段無可取得的大眾運輸路線，已改用駕車估算。"
                    "路線時間僅供參考，實際通勤時間請另行確認。"
                )
        else:
            # No route available yet — show preview / destination pin map
            destination_stop = st.session_state.get(SESSION_DESTINATION_STOP)
            must_visit_stops = st.session_state.get(SESSION_MUST_VISIT_STOPS, [])
            extra_dest_stops = st.session_state.get(SESSION_EXTRA_DEST_STOPS, [])
            render_preview_map(
                api_key,
                destination_stop=destination_stop,
                hotel_candidates=[],
                must_visit_stops=must_visit_stops,
                selected_hotel_place_id=None,
                additional_destination_stops=extra_dest_stops,
            )
            if not destination_stop and not must_visit_stops:
                st.caption("在右側輸入目的地後，地圖將顯示城市與景點預覽。")
    else:
        st.info("請先在 .env 設定 API 金鑰以顯示地圖。")

    st.divider()

    # ── Itinerary dashboard ───────────────────────────────────────────────
    if approved_days and workflow:
        _render_itinerary_dashboard(approved_days, workflow)
    elif workflow:
        st.caption("行程規劃進行中，核准的天次將在此顯示…")


def _render_itinerary_dashboard(approved_days: list, workflow: "TravelWorkflow") -> None:
    """Progressive disclosure: one st.expander per approved day with a visual timeline."""
    _require_streamlit()
    total = workflow.trip_spec.total_days
    st.subheader(f"📋 行程總覽（{len(approved_days)} / {total} 天）")

    fx = workflow.trip_spec.fx_snapshot
    currency = workflow.trip_spec.budget_currency

    for day_state in approved_days:
        city_label = f"【{day_state.destination}】" if day_state.destination else ""

        # Estimate daily cost in budget currency
        cost_str = ""
        if day_state.prices and fx:
            day_cost = sum(
                (p.amount_original or Decimal("0")) * fx.rate
                for p in day_state.prices
                if p.amount_original is not None and p.status is not PriceStatus.MISSING_PRICE
            )
            if day_cost > 0:
                cost_str = f"  💰 {day_cost:,.0f} {currency}"

        # Build a short theme label from place names
        places = day_state.places
        if places:
            if len(places) == 1:
                theme = places[0].name
            elif len(places) == 2:
                theme = f"{places[0].name} & {places[1].name}"
            else:
                theme = f"{places[0].name} 等 {len(places)} 個景點"
        else:
            theme = "規劃中"

        quality_str = (
            " ⭐" * day_state.quality_score if day_state.quality_score else ""
        )
        expander_title = (
            f"Day {day_state.day}: {city_label}{theme}{cost_str}{quality_str}"
        )

        with st.expander(expander_title, expanded=(day_state.day == approved_days[-1].day)):
            _render_day_timeline(day_state)

            if day_state.route:
                route = day_state.route
                cols = st.columns(3)
                cols[0].metric("移動", f"{route.total_required_transfer_minutes} 分")
                cols[1].metric(
                    "總距離",
                    f"{getattr(route, 'total_distance_km', 0.0):.1f} km",
                    delta=f"步行 {route.walking_distance_km:.1f} km",
                    delta_color="off",
                )
                cols[2].metric(
                    "品質",
                    f"{day_state.quality_score}/5" if day_state.quality_score else "—",
                )

    # Trip complete summary at the bottom
    if len(approved_days) == total:
        st.success(f"🎉 全程 {total} 天行程規劃完成！")
        try:
            _pdf_bytes = generate_itinerary_pdf(workflow.trip_spec, list(approved_days))
            _pdf_dest = workflow.trip_spec.destination.replace(" ", "_")
            st.download_button(
                label="📄 下載完整行程 PDF",
                data=_pdf_bytes,
                file_name=f"{_pdf_dest}_{total}天行程.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="pdf_dl_dashboard",
            )
        except Exception as _pdf_err:  # noqa: BLE001
            st.warning(f"PDF 產生失敗：{_pdf_err}")


def _render_day_timeline(day_state: "DayPlanState") -> None:
    """Render a visual time-axis timeline for one approved day.

    Prefers ``day_state.timeslots`` (rich TimeSlot schedule) when available.
    Falls back to synthesising rows from ``day_state.places`` and ``day_state.meals``
    so the old-style fixture output still renders gracefully.
    """
    # ── CSS injected once per page render (Streamlit deduplicates identical HTML) ──
    st.markdown(
        """
        <style>
        .tl-row {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin: 4px 0;
            font-size: 0.88rem;
            line-height: 1.4;
        }
        .tl-time {
            flex: 0 0 110px;
            font-family: monospace;
            color: #888;
            white-space: nowrap;
            padding-top: 1px;
        }
        .tl-dot-attraction {
            flex: 0 0 18px;
            text-align: center;
        }
        .tl-dot-meal {
            flex: 0 0 18px;
            text-align: center;
        }
        .tl-dot-free {
            flex: 0 0 18px;
            text-align: center;
        }
        .tl-label-attraction {
            font-weight: 600;
            color: #1a73e8;
        }
        .tl-label-meal {
            font-weight: 600;
            color: #e67e22;
        }
        .tl-label-free {
            color: #888;
            font-style: italic;
        }
        .tl-tag {
            display: inline-block;
            font-size: 0.72rem;
            padding: 1px 6px;
            border-radius: 10px;
            margin-left: 6px;
            vertical-align: middle;
        }
        .tl-tag-attraction { background:#e8f0fe; color:#1a73e8; }
        .tl-tag-lunch      { background:#fef3e2; color:#e67e22; }
        .tl-tag-dinner     { background:#fde8d8; color:#c0392b; }
        .tl-tag-free       { background:#f1f3f4; color:#888; }
        .tl-connector {
            width: 2px;
            background: #e0e0e0;
            height: 10px;
            margin-left: 109px;
        }
        .tl-transit {
            margin: 1px 0 1px 113px;
            color: #aaa;
            font-size: 0.76rem;
            line-height: 1.4;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    rows_html: list[str] = []

    def _row(time_str: str, icon: str, label: str, tag_html: str, label_class: str, dot_class: str) -> str:
        return (
            f'<div class="tl-row">'
            f'<span class="tl-time">{time_str}</span>'
            f'<span class="{dot_class}">{icon}</span>'
            f'<span class="{label_class}">{label}{tag_html}</span>'
            f'</div>'
        )

    def _connector() -> str:
        return '<div class="tl-connector"></div>'

    def _transit_row(gap_min: int, route: object) -> str:
        """Return an HTML transit-indicator row between two adjacent TimeSlots."""
        if route is None or gap_min <= 5:
            return _connector()
        is_drive = "drive" in (getattr(route, "source_provider", "") or "").lower()
        t_icon = "🚗" if is_drive else "🚃"
        t_label = "駕車" if is_drive else "大眾運輸"
        return (
            f'<div class="tl-transit">'
            f"⬇&nbsp; {t_icon} {t_label}（{gap_min} 分鐘）"
            f"</div>"
        )

    # ── Prefer rich TimeSlot schedule ──────────────────────────────────────
    if day_state.timeslots:
        for i, slot in enumerate(day_state.timeslots):
            time_str = f"{slot.start_time} – {slot.end_time}"
            name = slot.location_name or "（未知地點）"

            if slot.slot_type is SlotType.ATTRACTION:
                rows_html.append(_row(
                    time_str, "📍", name,
                    '<span class="tl-tag tl-tag-attraction">景點</span>',
                    "tl-label-attraction", "tl-dot-attraction",
                ))
            elif slot.slot_type is SlotType.LUNCH:
                # Food Agent filled this slot with a confirmed restaurant
                rows_html.append(_row(
                    time_str, "🍽️", name,
                    '<span class="tl-tag tl-tag-lunch">午餐</span>',
                    "tl-label-meal", "tl-dot-meal",
                ))
            elif slot.slot_type is SlotType.DINNER:
                # Food Agent filled this slot with a confirmed restaurant
                rows_html.append(_row(
                    time_str, "🍷", name,
                    '<span class="tl-tag tl-tag-dinner">晚餐</span>',
                    "tl-label-meal", "tl-dot-meal",
                ))
            elif slot.slot_type is SlotType.LUNCH_PLACEHOLDER:
                # Still a placeholder — food agent didn't fill it
                rows_html.append(_row(
                    time_str, "🍽️", name,
                    '<span class="tl-tag tl-tag-lunch">午餐（待定）</span>',
                    "tl-label-meal", "tl-dot-meal",
                ))
            elif slot.slot_type is SlotType.DINNER_PLACEHOLDER:
                rows_html.append(_row(
                    time_str, "🍷", name,
                    '<span class="tl-tag tl-tag-dinner">晚餐（待定）</span>',
                    "tl-label-meal", "tl-dot-meal",
                ))
            elif slot.slot_type is SlotType.FREE_TIME:
                rows_html.append(_row(
                    time_str, "☕", name or "自由時間",
                    '<span class="tl-tag tl-tag-free">自由</span>',
                    "tl-label-free", "tl-dot-free",
                ))

            # Transit indicator between slots (except after the last)
            if i < len(day_state.timeslots) - 1:
                next_slot = day_state.timeslots[i + 1]
                _sh, _sm = map(int, slot.end_time.split(":"))
                _eh, _em = map(int, next_slot.start_time.split(":"))
                _gap = (_eh * 60 + _em) - (_sh * 60 + _sm)
                rows_html.append(_transit_row(_gap, day_state.route))

    else:
        # ── Fallback: synthesise from place + meal lists (old-style) ──────
        LOAD_TAG_HOURS = {"FULL_DAY_HIGH_LOAD": 5, "LONG_VISIT": 3, "DAY_TRIP": 7}
        hour = 9

        for i, place in enumerate(day_state.places):
            load = getattr(place, "load_tag", None)
            load_str = str(load) if load else ""
            duration = LOAD_TAG_HOURS.get(load_str, 2)
            end_hour = hour + duration
            time_str = f"{hour:02d}:00 – {end_hour:02d}:00"
            icon = "🎡" if "HIGH_LOAD" in load_str else "📍"
            rows_html.append(_row(
                time_str, icon, place.name,
                '<span class="tl-tag tl-tag-attraction">景點</span>',
                "tl-label-attraction", "tl-dot-attraction",
            ))
            hour = end_hour + 1  # 1-hour buffer
            if i < len(day_state.places) + len(day_state.meals) - 1:
                rows_html.append(_connector())

        # Meals appear at noon / 18:30 respectively
        meal_times = ["12:00 – 13:30", "18:30 – 20:00"]
        meal_icons = ["🍜", "🍷"]
        meal_tags = [
            '<span class="tl-tag tl-tag-lunch">午餐</span>',
            '<span class="tl-tag tl-tag-dinner">晚餐</span>',
        ]
        for j, meal in enumerate(day_state.meals):
            if rows_html:
                rows_html.append(_connector())
            time_str = meal_times[j] if j < len(meal_times) else "— "
            icon = meal_icons[j] if j < len(meal_icons) else "🍽️"
            tag = meal_tags[j] if j < len(meal_tags) else '<span class="tl-tag tl-tag-lunch">餐廳</span>'
            rows_html.append(_row(time_str, icon, meal.name, tag, "tl-label-meal", "tl-dot-meal"))

    if rows_html:
        st.markdown("\n".join(rows_html), unsafe_allow_html=True)
    else:
        st.caption("行程詳情載入中…")


# ── Map Pin Helpers ───────────────────────────────────────────────────────────

def _ground_macro_key_places(
    settings: "Settings",
    city_segments: list,
    max_pins: int = 5,
) -> list[PlaceStop]:
    """Ground key_places from macro city_segments into PlaceStop objects for map display.

    Grounds at most ``max_pins`` places in total (across all cities),
    silently skipping any that the Places API cannot resolve.
    """
    if settings is None:
        return []
    try:
        api_key = settings.google_maps_api_key.get_secret_value()
    except Exception:
        return []

    places_client = GooglePlacesClient(api_key)
    stops: list[PlaceStop] = []
    for seg in city_segments:
        key_places: list[str] = getattr(seg, "key_places", []) or []
        for place_name in key_places:
            if len(stops) >= max_pins:
                break
            try:
                stop = places_client.ground(place_name)
                stops.append(stop)
            except Exception:
                # Skip unresolvable places gracefully
                continue
        if len(stops) >= max_pins:
            break
    return stops


# ── Right Interactions ────────────────────────────────────────────────────────

def _render_right_interactions(
    workflow: "TravelWorkflow | None",
    result: "WorkflowResult | None",
    settings: "Settings | None",
) -> None:
    """Right 4-column panel: chat onboarding (Phase 0), macro decision (Phase 1), day decisions (Phase 2)."""
    _require_streamlit()

    # Phase 0 — no active workflow: conversational onboarding
    if workflow is None or result is None:
        # Settings may not be in session yet on first render — load eagerly
        if settings is None:
            try:
                settings = Settings()
                st.session_state[SESSION_SETTINGS] = settings
            except (ValidationError, ValueError, InvalidOperation) as e:
                st.error(f"⚠️ 無法載入 API 設定：{e}")
                st.info("請確認 .env 中已設定所有必要的 API 金鑰後重新啟動。")
                return
            except Exception as e:  # pragma: no cover
                st.error(f"⚠️ 初始化失敗：{e}")
                return
        _render_phase0_chat(settings)
        return

    # Phase 1 & 2: unified chat-based decision UI

    # Ensure shared session state is initialised
    st.session_state.setdefault(SESSION_CHAT_HISTORY, [])
    st.session_state.setdefault(SESSION_MACRO_HOTELS, {})
    st.session_state.setdefault(SESSION_MACRO_STEP, 0)

    macro_step: int = st.session_state[SESSION_MACRO_STEP]
    macro_hotels: dict = st.session_state[SESSION_MACRO_HOTELS]
    cities = _get_cities_for_hotel_selection(workflow)
    all_macro_confirmed = len(macro_hotels) >= len(cities)
    # Two-step macro flow: True once user approves the plan overview
    plan_approved: bool = bool(st.session_state.get(SESSION_MACRO_PLAN_APPROVED, False))

    # ── Task A2: Populate map pins from macro plan key_places (once only) ──
    # When AWAITING_MACRO_DECISION and map pins haven't been populated yet,
    # ground up to 5 key_places from the macro city_segments and store them.
    _MACRO_PINS_FLAG = "_macro_pins_populated"
    if (
        result.status is WorkflowStatus.AWAITING_MACRO_DECISION
        and not st.session_state.get(_MACRO_PINS_FLAG)
        and workflow.macro_plan
        and workflow.macro_plan.city_segments
    ):
        _key_place_stops = _ground_macro_key_places(
            settings, workflow.macro_plan.city_segments, max_pins=5
        )
        st.session_state[SESSION_EXTRA_DEST_STOPS] = _key_place_stops
        st.session_state[_MACRO_PINS_FLAG] = True

    # Inject assistant decision-prompt once per unique state
    decision_key = _make_decision_key(result, macro_step, all_macro_confirmed, plan_approved)
    if st.session_state.get(SESSION_DECISION_KEY) != decision_key:
        msg = _build_decision_message(
            result, workflow, macro_step, all_macro_confirmed, plan_approved
        )
        if msg:
            st.session_state[SESSION_CHAT_HISTORY].append(
                {"role": "assistant", "content": msg}
            )
        st.session_state[SESSION_DECISION_KEY] = decision_key

    # ── Context-aware chat input placeholder ──────────────────────────────
    if result.status is WorkflowStatus.AWAITING_MACRO_DECISION:
        if not plan_approved:
            _ph = "輸入「1」確認行程，或「2」＋修改意見返回調整…"
        elif all_macro_confirmed:
            _ph = "輸入「確認」開始細部規劃，或「返回」重新調整…"
        else:
            _ph = "輸入 1 / 2 / 3 選飯店，飯店名稱，或說「隨便」…"
    elif result.status is WorkflowStatus.AWAITING_DUPLICATE_DECISION:
        _ph = "輸入「再次造訪」或「換一個」…"
    elif result.status is WorkflowStatus.AWAITING_PACE_DECISION:
        _ph = "輸入「重新規劃」、「接受」或「調整節奏」…"
    elif result.status is WorkflowStatus.AWAITING_BUDGET_DECISION:
        _ph = "輸入「接受超支」、「壓低費用」或「換景點」…"
    elif result.status is WorkflowStatus.AWAITING_PRICE_DECISION:
        _ph = "輸入任意內容接受並繼續…"
    elif result.status is WorkflowStatus.DAY_APPROVED:
        _ph = "輸入「下一天」或「繼續」…"
    elif result.status is WorkflowStatus.NEEDS_MANUAL_REVIEW:
        _ph = "輸入「重新規劃」或「返回上一天」…"
    else:
        _ph = "輸入您的選擇…"

    # ── Reserve message area ABOVE the chat input (same pattern as Phase 0) ──
    messages_container = st.container()

    # Chat input — Streamlit pins this to the bottom of the viewport
    phase12_input = st.chat_input(_ph)

    with messages_container:
        # Compact agent-step progress (collapsible transparency panel)
        with st.expander("📊 驗證進度", expanded=False):
            step_statuses = _workflow_step_statuses(result)
            stat_cols = st.columns(len(step_statuses))
            for col, (label, status_label) in zip(stat_cols, step_statuses.items()):
                col.metric(label, status_label)

        # Render full chat history
        for chat_msg in st.session_state[SESSION_CHAT_HISTORY]:
            with st.chat_message(chat_msg["role"]):
                st.markdown(chat_msg["content"])

        # If user typed something, render it inline and dispatch to handler
        if phase12_input:
            with st.chat_message("user"):
                st.markdown(phase12_input)
            _handle_phase12_chat_input(
                phase12_input, workflow, result, settings,
                macro_step, cities, all_macro_confirmed,
            )
            # _handle_phase12_chat_input always ends in st.rerun(); nothing runs below here


def _render_phase0_chat(settings: Settings) -> None:
    """Phase 0: conversational onboarding — chat-based TripSpec collection."""
    _require_streamlit()

    # ── Init session state ────────────────────────────────────────────────
    if SESSION_CHAT_HISTORY not in st.session_state:
        st.session_state[SESSION_CHAT_HISTORY] = [
            {"role": "assistant", "content": _WELCOME_MESSAGE}
        ]
    if SESSION_COLLECTED_PARAMS not in st.session_state:
        st.session_state[SESSION_COLLECTED_PARAMS] = {}

    # ── Reserve message area ABOVE the chat input ─────────────────────────
    # st.container() is declared first so its position in the layout is fixed
    # above the chat_input bar.  All chat bubbles (history, new user message,
    # spinner, AI reply) are rendered INTO this container so they are always
    # visible above the pinned input — never below it.
    messages_container = st.container()

    # ── Chat input — Streamlit pins this to the bottom of the viewport ────
    user_input = st.chat_input("請告訴我你的旅遊計畫…")

    # ── Render all chat content inside the container ──────────────────────
    with messages_container:
        # Existing history
        for msg in st.session_state[SESSION_CHAT_HISTORY]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if not user_input:
            return  # Nothing new — history already drawn, input waiting

        # Immediately show the user's message (same render cycle, no extra rerun)
        st.session_state[SESSION_CHAT_HISTORY].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # ── Call LLM intent parser ────────────────────────────────────────
        # Signal Intent Parser is running in the sidebar metrics table
        _m0 = st.session_state.get(SESSION_METRICS)
        if _m0 and "Intent Parser" in _m0:
            _m0["Intent Parser"]["status"] = "🟢 Running"
            _upd0 = st.session_state.get(SESSION_SIDEBAR_UPDATERS, {}).get("redraw")
            if _upd0:
                _upd0(_m0)
        with st.spinner("AI 正在理解您的需求…"):
            try:
                agents = LiveAgentSuite(settings)
                agents.set_metrics_callback(_make_metrics_cb())
                parse_result: IntentParseResult = agents.parse_user_intent(
                    chat_history=st.session_state[SESSION_CHAT_HISTORY],
                    current_params=st.session_state[SESSION_COLLECTED_PARAMS],
                )
            except Exception as e:
                st.session_state[SESSION_CHAT_HISTORY].append({
                    "role": "assistant",
                    "content": f"抱歉，AI 解析時發生錯誤，請稍後再試。（{e}）",
                })
                st.rerun()
                return

    # ── Merge extracted fields into collected params (state only, no UI) ──
    params: dict = st.session_state[SESSION_COLLECTED_PARAMS]
    for field in (
        "destination", "days", "budget_amount", "budget_currency",
        "interests", "must_visit_names", "must_visit_price", "pace_label",
        "traveler_type",
    ):
        value = getattr(parse_result, field, None)
        # Only overwrite when the LLM returned a non-empty / non-zero value
        if value is not None and value != "" and value != 0:
            params[field] = value

    # Accumulate user_constraints across turns (never overwrite, only append)
    new_constraint = getattr(parse_result, "user_constraints", "") or ""
    if new_constraint.strip():
        existing = st.session_state.get(SESSION_USER_CONSTRAINTS, [])
        if new_constraint not in existing:
            existing = existing + [new_constraint.strip()]
        st.session_state[SESSION_USER_CONSTRAINTS] = existing

    st.session_state[SESSION_COLLECTED_PARAMS] = params

    # ── Route based on parse result ───────────────────────────────────────
    if parse_result.is_ready:
        _launch_workflow_from_chat(settings, params)
        # _launch_workflow_from_chat calls st.rerun() on success or appends an error
        return

    if parse_result.needs_clarification and parse_result.clarification_question:
        st.session_state[SESSION_CHAT_HISTORY].append({
            "role": "assistant",
            "content": parse_result.clarification_question,
        })
    else:
        # Fallback — LLM didn't raise a question but info is incomplete
        missing = []
        if not params.get("destination"):
            missing.append("目的地")
        if not params.get("days"):
            missing.append("旅遊天數")
        if not params.get("budget_amount"):
            missing.append("預算")
        if missing:
            st.session_state[SESSION_CHAT_HISTORY].append({
                "role": "assistant",
                "content": f"我還需要您提供：{'、'.join(missing)}。能再告訴我一些嗎？",
            })

    st.rerun()


def _launch_workflow_from_chat(settings: Settings, params: dict) -> None:
    """Build TripSpec from chat-collected params and start the macro workflow."""
    _require_streamlit()

    # ── Map pace label → PaceLevel ────────────────────────────────────────
    # Default to STANDARD ("一般") — the LLM uses it whenever user has no preference
    pace_map = {v: k for k, v in PACE_LABELS.items()}
    pace_level = pace_map.get(params.get("pace_label", ""), PaceLevel.STANDARD)

    destination = str(params.get("destination", "")).strip()
    days_raw = params.get("days", 5)
    budget_amount = str(params.get("budget_amount", "25000"))
    budget_currency = str(params.get("budget_currency", "TWD"))
    interests = str(params.get("interests", ""))
    must_visit_name = str(params.get("must_visit_names", "")).strip()
    must_visit_price = str(params.get("must_visit_price", "")).strip()

    try:
        _validate_trip_submission_inputs(
            destination=destination,
            days=int(days_raw),
            budget_amount=budget_amount,
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
        )
        # Ground must-visit stops (if any)
        must_visit_stops, must_visit_errors = _preview_must_visit_stops(
            settings, must_visit_name
        )
        if must_visit_errors:
            for err in must_visit_errors:
                st.session_state[SESSION_CHAT_HISTORY].append({
                    "role": "assistant",
                    "content": f"⚠️ {err}",
                })
            st.rerun()
            return

        user_constraints = st.session_state.get(SESSION_USER_CONSTRAINTS, [])
        trip_spec = _build_trip_spec_from_preflight(
            settings=settings,
            destination=destination,
            days=int(days_raw),
            budget_amount=budget_amount,
            budget_currency=budget_currency,
            interests=interests,
            pace_level=pace_level,
            route_mode=RouteMode.AUTO,
            walking_preference=WalkingPreference.NORMAL,
            grounded_must_visit=must_visit_stops,
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
            must_visit_price_url="",
            user_constraints=user_constraints,
        )
    except (ValidationError, ValueError, InvalidOperation) as e:
        st.session_state[SESSION_CHAT_HISTORY].append({
            "role": "assistant",
            "content": (
                f"⚠️ 資料有誤：{e}\n\n"
                "請再確認目的地、天數或預算的格式，例如：\n"
                "> 「大阪 5 天，預算 3 萬台幣」"
            ),
        })
        st.rerun()
        return

    # Update map preview destination for the left dashboard
    destination_stop = _preview_destination(settings, destination)
    st.session_state[SESSION_DESTINATION_STOP] = destination_stop
    st.session_state[SESSION_MUST_VISIT_STOPS] = must_visit_stops

    # Launch workflow
    workflow = _build_live_workflow(settings, trip_spec)
    st.session_state[SESSION_SETTINGS] = settings
    st.session_state[SESSION_WORKFLOW] = workflow
    st.session_state.pop(SESSION_MACRO_HOTELS, None)
    st.session_state.pop(SESSION_MACRO_STEP, None)
    st.session_state.pop(SESSION_MACRO_PLAN_APPROVED, None)
    # Do NOT reset SESSION_METRICS here — preserve Intent Parser token data accumulated
    # during Phase 0 onboarding.  Just clear any lingering "🟢 Running" markers.
    _m_launch = st.session_state.get(SESSION_METRICS)
    if _m_launch:
        for _entry in _m_launch.values():
            _entry["status"] = "💤 Idle"
    # Wire metrics callback so validate_macro's propose_macro_plan LLM call is tracked
    _set_mcb = getattr(workflow.agents, "set_metrics_callback", None)
    if _set_mcb is not None:
        _set_mcb(_make_metrics_cb())
    st.session_state[SESSION_RESULT] = workflow.validate_macro()
    st.session_state.pop(SESSION_ERROR, None)
    # Clear stale map markers from any previous destination
    st.session_state[SESSION_EXTRA_DEST_STOPS] = []
    st.session_state[SESSION_MUST_VISIT_ERRORS] = []
    st.session_state.pop("_macro_pins_populated", None)
    st.rerun()


# ── Phase 1 & 2 Chat Decision Helpers ────────────────────────────────────────

def _make_decision_key(
    result: "WorkflowResult",
    macro_step: int,
    all_macro_confirmed: bool,
    plan_approved: bool = False,
) -> str:
    """Unique key for the current decision state — controls when to inject a new message."""
    day = result.day_state.day if result.day_state else 0
    if result.status is WorkflowStatus.AWAITING_MACRO_DECISION:
        # Step 1: overview (plan_approved=False)  Step 2: hotel pick (plan_approved=True)
        plan_step = "hotels" if plan_approved else "overview"
        suffix = "done" if all_macro_confirmed else str(macro_step)
        return f"{result.status.value}|day={day}|macro={suffix}|step={plan_step}"
    return f"{result.status.value}|day={day}"


def _build_decision_message(
    result: "WorkflowResult",
    workflow: "TravelWorkflow",
    macro_step: int,
    all_macro_confirmed: bool,
    plan_approved: bool = False,
) -> str | None:
    """Build the assistant message to inject when entering a new decision state."""
    status = result.status

    if status is WorkflowStatus.AWAITING_MACRO_DECISION:
        if not plan_approved:
            # Step 1: show plan overview only; do NOT show hotels yet
            return _build_macro_plan_overview_message(result, workflow)
        if all_macro_confirmed:
            return None  # ack already injected by _try_select_hotel_in_chat
        # Step 2: hotel selection
        return _build_macro_hotel_selection_message(result, workflow, macro_step)

    if status is WorkflowStatus.AWAITING_PACE_DECISION:
        return _build_pace_chat_message(result, workflow)

    if status is WorkflowStatus.AWAITING_BUDGET_DECISION:
        return _build_budget_chat_message(result, workflow)

    if status is WorkflowStatus.AWAITING_PRICE_DECISION:
        return (
            "📋 目前仍有部分費用尚未確認（例如票價資訊不完整）。\n\n"
            "請選擇：\n"
            "1. 接受價格未確認的警告，繼續規劃\n"
            "（直接輸入數字或任意文字均可）"
        )

    if status is WorkflowStatus.AWAITING_DUPLICATE_DECISION:
        return _build_duplicate_chat_message(result)

    if status is WorkflowStatus.DAY_APPROVED:
        return _build_approved_chat_message(result, workflow)

    if status is WorkflowStatus.NEEDS_MANUAL_REVIEW:
        day = result.day_state.day if result.day_state else 1
        reason = _format_manual_review_reason(result)
        return (
            f"⚠️ **此日行程需要人工確認**\n\n"
            f"🛑 **卡關原因**：{reason}\n\n"
            f"請選擇：\n"
            f"1. 重新規劃（換一批不同景點）\n"
            f"2. 返回上一天（回到第 {max(1, day - 1)} 天重新選擇）\n"
            f"（直接輸入數字或文字均可）"
        )

    return None


def _build_macro_plan_overview_message(
    result: "WorkflowResult",
    workflow: "TravelWorkflow",
) -> str:
    """Step 1 of macro phase: show city/theme overview only — no hotels yet."""
    macro = workflow.macro_plan
    lines = ["根據您的需求，我已初步規劃了行程架構 🗺️\n"]

    if macro and macro.city_segments:
        for seg in macro.city_segments:
            lines.append(f"**🏙️ {seg.city}**（{seg.day_range}）")
            lines.append(f"> 主題：{seg.theme}")
            if seg.key_places:
                lines.append(
                    f"> 📍 景點亮點（僅列出代表性地標，詳細每日景點將於下一步排滿）："
                    f"{'、'.join(seg.key_places[:3])}"
                )
            if seg.food_picks:
                lines.append(f"> 必吃美食：{'、'.join(seg.food_picks[:2])}")
            if seg.notes:
                lines.append(f"> 小提醒：{seg.notes}")
            lines.append("")
    elif macro and macro.day_slots:
        for slot in macro.day_slots:
            label = "🚆 移動日" if slot.is_travel_day else slot.city
            lines.append(f"- 第 {slot.day} 天：{label}")
        lines.append("")

    if result.conflict and result.conflict.reasons:
        lines.append("⚠️ **注意事項：**")
        for reason in result.conflict.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("---\n\n請問這個初步行程架構可以嗎？")
    lines.append("1. ✅ 確認，幫我找住宿")
    lines.append("2. ↩️ 返回修改（可直接在後面輸入調整意見，例如「2 把大阪改成京都」）")
    lines.append("\n（直接輸入數字或文字均可）")

    return "\n".join(lines)


def _build_macro_hotel_selection_message(
    result: "WorkflowResult",
    workflow: "TravelWorkflow",
    macro_step: int,
) -> str:
    """Step 2 of macro phase: hotel selection for the current city."""
    macro = workflow.macro_plan
    cities = _get_cities_for_hotel_selection(workflow)
    current_city = cities[macro_step] if macro_step < len(cities) else cities[-1]
    seg = next((s for s in (macro.city_segments or []) if s.city == current_city), None)

    lines = [f"🏨 **請為「{current_city}」選擇住宿**（第 {macro_step + 1} / {len(cities)} 個城市）\n"]

    if seg and seg.hotel_candidates:
        descriptions = getattr(seg, "hotel_descriptions", None) or []
        for i, name in enumerate(seg.hotel_candidates[:3], 1):
            desc = descriptions[i - 1] if i - 1 < len(descriptions) else ""
            desc_suffix = f"  _（{desc}）_" if desc else ""
            lines.append(f"{i}. **{name}**{desc_suffix}")
        lines.append("4. 其他（直接輸入飯店名稱）")
        lines.append("5. 隨便（讓 AI 幫我選）")
    else:
        lines.append("請在下方輸入您偏好的住宿名稱，或說「隨便」讓我幫您選。")

    lines.append("\n（直接輸入數字或文字均可）")
    return "\n".join(lines)


def _extract_bottleneck(conflict: "ConflictEvent | None") -> str:
    """從 ConflictEvent.evidence 提取瓶頸路段描述文字。

    若 evidence 中有 bottleneck_orig / bottleneck_dest / bottleneck_minutes，
    回傳格式化字串；否則回傳空字串。
    """
    if conflict is None:
        return ""
    ev = conflict.evidence
    orig = ev.get("bottleneck_orig")
    dest = ev.get("bottleneck_dest")
    minutes = ev.get("bottleneck_minutes")
    if orig and dest and minutes is not None:
        return f"從「{orig}」到「{dest}」單趟需耗時約 **{minutes} 分鐘**。"
    return ""


def _build_pace_chat_message(
    result: "WorkflowResult", workflow: "TravelWorkflow"
) -> str:
    route = result.day_state.route if result.day_state else None
    day = result.day_state.day if result.day_state else "?"
    conflict = result.conflict
    reasons = conflict.reasons if conflict else []

    # Choose the detail message based on whichever constraint was actually violated.
    if "walking_distance_short_walk_only" in reasons:
        observed_km = route.walking_distance_km if route else 0
        limit_km = workflow.trip_spec.pace.walking_distance_warning_km * 0.5
        detail = (
            f"今日步行距離 **{observed_km:.1f} km**，"
            f"超過「僅短距離步行」模式上限 {limit_km:.1f} km。"
        )
    elif "total_required_transfer_minutes" in reasons:
        observed = int(route.total_required_transfer_minutes) if route else "?"
        limit = workflow.trip_spec.pace.max_required_transfer_minutes_per_day
        pace_name = _pace_label_for_limit(limit)
        detail = (
            f"今日必要移動時間 **{observed} 分鐘**，"
            f"超過{pace_name}模式上限 {limit} 分鐘。"
        )
        bottleneck = _extract_bottleneck(conflict)
        if bottleneck:
            detail += f"\n\n🔍 **耗時瓶頸分析**：\n{bottleneck}"
    elif "max_single_transfer_minutes" in reasons:
        observed = int(route.max_single_transfer_minutes) if route else "?"
        limit = workflow.trip_spec.pace.max_single_transfer_minutes
        detail = (
            f"單段移動時間 **{observed} 分鐘**，"
            f"超過單段上限 {limit} 分鐘。"
        )
        bottleneck = _extract_bottleneck(conflict)
        if bottleneck:
            detail += f"\n\n🔍 **耗時瓶頸分析**：\n{bottleneck}"
    elif "major_place_count" in reasons or "full_day_high_load" in reasons:
        detail = "今日景點安排超出節奏限制（景點數量過多或含全日高負荷景點與其他景點衝突）。"
    else:
        # schedule_timing_conflict: reasons contain descriptive gap strings
        detail = reasons[0] if reasons else "行程時間安排需要調整。"

    return (
        f"🏃 **Day {day} — 節奏提示**\n\n{detail}\n\n"
        "請選擇調整方式：\n"
        "1. 重新規劃（換景點，保持原節奏）\n"
        "2. 接受（這天累一點沒關係）\n"
        "3. 調整節奏（改為一般模式）\n"
        "（直接輸入數字或文字均可）"
    )


def _build_budget_chat_message(
    result: "WorkflowResult", workflow: "TravelWorkflow"
) -> str:
    day = result.day_state.day if result.day_state else "?"
    currency = workflow.trip_spec.budget_currency
    outcome = evaluate_budget(
        workflow.trip_spec.prices + result.day_state.prices,
        workflow.trip_spec.fx_snapshot,
        workflow.trip_spec.budget_override_history[-1]
        if workflow.trip_spec.budget_override_history
        else workflow.trip_spec.budget_amount,
    )
    return (
        f"💰 **Day {day} — 預算提示**\n\n"
        f"已確認費用：**{currency} {outcome.confirmed_total}**\n"
        f"區間上限：**{currency} {outcome.maximum_total}**\n\n"
        "請選擇調整方式：\n"
        "1. 接受超支（繼續目前行程）\n"
        "2. 壓低費用（保留必去景點，削減其他開銷）\n"
        "3. 換景點（維持預算，更換部分景點）\n"
        "4. 增加預算（輸入「4 增加到X萬」，例如「4 增加到5萬」）\n"
        "（直接輸入數字或文字均可）"
    )


def _build_duplicate_chat_message(result: "WorkflowResult") -> str:
    conflict = result.conflict
    day = result.day_state.day if result.day_state else "?"
    name = conflict.evidence.get("duplicate_place_name", "（未知地點）") if conflict else "（未知地點）"
    return (
        f"🔄 **Day {day} — 重複景點提示**\n\n"
        f"「**{name}**」在之前的行程中已安排過了。\n\n"
        "請選擇：\n"
        "1. 是，再次造訪\n"
        "2. 換一個新景點\n"
        "（直接輸入數字或文字均可）"
    )


def _build_approved_chat_message(
    result: "WorkflowResult", workflow: "TravelWorkflow"
) -> str:
    day_state = result.day_state
    day = day_state.day
    fx = workflow.trip_spec.fx_snapshot
    currency = workflow.trip_spec.budget_currency

    place_str = (
        "、".join(p.name for p in day_state.places)
        if day_state.places else "（景點載入中）"
    )

    lines = [f"✅ **Day {day} — 行程核准！**\n\n景點：{place_str}"]

    if day_state.meals:
        lines.append("餐廳：" + "、".join(m.name for m in day_state.meals))

    if day_state.route:
        r = day_state.route
        lines.append(
            f"移動 {r.total_required_transfer_minutes} 分鐘 ｜ 總距離 {getattr(r, 'total_distance_km', 0.0):.1f} km（其中步行 {r.walking_distance_km:.1f} km）"
        )

    if day_state.prices and fx:
        day_cost = sum(
            (p.amount_original or Decimal("0")) * fx.rate
            for p in day_state.prices
            if p.amount_original is not None and p.status is not PriceStatus.MISSING_PRICE
        )
        if day_cost > 0:
            lines.append(f"今日費用：約 **{currency} {day_cost:,.0f}**")

    try:
        mem = workflow.global_memory
        if mem.remaining_budget >= 0 and fx:
            lines.append(f"剩餘預算：**{currency} {mem.remaining_budget:,.0f}**")
    except Exception:
        pass

    if day_state.quality_score is not None:
        lines.append(f"品質評分：{'⭐' * day_state.quality_score} {day_state.quality_score}/5")

    if day_state.warnings:
        import re as _re_warn
        # Filter out internal system labels (ALL_CAPS_WITH_UNDERSCORES like
        # USER_ACCEPTED_..., KEEP_PACE_..., REVIEWER_VETO..., REJECT_..., etc.)
        # and private markers that start with "_".
        user_warnings = [
            w for w in day_state.warnings
            if not w.startswith("_")
            and not _re_warn.fullmatch(r"[A-Z][A-Z0-9_]+", w)
        ]
        if user_warnings:
            lines.append("⚠️ 注意：" + "、".join(user_warnings))

    total = workflow.trip_spec.total_days
    if _can_plan_next_day(current_day=day, total_days=total):
        lines.append("\n請選擇：")
        lines.append(f"1. ▶️ 繼續規劃第 {day + 1} 天")
        lines.append("（直接輸入數字或「下一天」均可）")
    else:
        lines.append(f"\n🎉 **恭喜！全程 {total} 天行程規劃完成！**")

    return "\n".join(lines)


def _interpret_macro_hotel_input(text: str, candidates: list[str]) -> str | None:
    """Map a free-text chat message to a hotel name.

    Returns:
        A hotel name string (one of the candidates, or a custom name typed by the user),
        or None if the input couldn't be understood.
    """
    import re as _re

    t = text.strip()
    low = t.lower()

    # ── 1. Vague / "no-preference" triggers → pick first candidate ────────
    # Long triggers: substring match is fine (unlikely to appear in a hotel name)
    long_triggers = (
        "隨便", "都可以", "你決定", "你幫我決定", "幫我決定", "隨意", "無所謂",
        "不管", "交給你", "第一個", "第一間", "都行", "隨你", "你選", "你挑",
        "whatever",
    )
    # Short triggers: only match if the *entire* input is exactly this phrase
    short_triggers = ("any", "ok", "好", "可以", "隨")
    if any(tr in low for tr in long_triggers) or low in short_triggers:
        return candidates[0] if candidates else None

    # ── 2. Pure digit or bracketed digit: "1"–"3" → hotel; "5" → random ──
    m = _re.match(r'^[（(【「]?\s*([1-5])\s*[）)】」]?$', t)
    if m:
        digit = int(m.group(1))
        if digit == 5:
            return candidates[0] if candidates else None  # "隨便"
        if digit == 4:
            return None  # "4 = 自訂" — caller handles this separately
        idx = digit - 1
        return candidates[idx] if idx < len(candidates) else None

    # ── 3. Short phrase with an embedded digit: "選1", "第2間", "1也行" ───
    m = _re.search(r'(?:第|選|要|選擇|我選|我要)?([1-3])(?:個|間|號|可以|也行|好|ok|也可以)?$',
                   t, _re.IGNORECASE)
    if m and len(t) <= 8:
        idx = int(m.group(1)) - 1
        return candidates[idx] if idx < len(candidates) else None

    # ── 4. Longer text → treat as a custom hotel name ─────────────────────
    if len(t) >= 3:
        return t  # caller will pass to Google Places grounding

    return None


def _handle_phase12_chat_input(
    text: str,
    workflow: "TravelWorkflow",
    result: "WorkflowResult",
    settings: Settings,
    macro_step: int,
    cities: list[str],
    all_macro_confirmed: bool,
) -> None:
    """Interpret a free-text chat message during Phase 1/2 decision states and act on it.

    Every branch either calls st.rerun() (via a helper) or appends an assistant
    clarification and then calls st.rerun(), so execution never continues past this function.
    """
    status = result.status
    t = text.strip()
    low = t.lower()

    # ── AWAITING_MACRO_DECISION ───────────────────────────────────────────
    if status is WorkflowStatus.AWAITING_MACRO_DECISION:
        macro_hotels: dict = st.session_state.get(SESSION_MACRO_HOTELS, {})
        plan_approved = bool(st.session_state.get(SESSION_MACRO_PLAN_APPROVED, False))

        # Helper: extract and store constraint text after a "back/modify" keyword
        def _extract_and_store_constraint(raw: str, triggers: tuple) -> None:
            constraint = raw
            for tr in triggers:
                constraint = constraint.replace(tr, "").strip("，,、 \t")
            # Also strip leading/trailing digits that were the selection prefix (e.g. "2 ")
            import re as _re2
            constraint = _re2.sub(r"^[12]\s*", "", constraint).strip()
            if constraint:
                existing = st.session_state.get(SESSION_USER_CONSTRAINTS, [])
                if constraint not in existing:
                    existing = existing + [constraint]
                st.session_state[SESSION_USER_CONSTRAINTS] = existing

        def _do_back_to_phase0(constraint_source: str, triggers: tuple) -> None:
            _extract_and_store_constraint(constraint_source, triggers)
            for key in [
                SESSION_WORKFLOW, SESSION_RESULT,
                SESSION_MACRO_HOTELS, SESSION_MACRO_STEP, SESSION_DECISION_KEY,
                SESSION_APPROVED_DAYS, "_macro_pins_populated",
                SESSION_MACRO_PLAN_APPROVED,
            ]:
                st.session_state.pop(key, None)
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": "好的，我們重新調整您的計畫。請告訴我您想修改什麼？",
            })
            st.rerun()

        # ── Step 1: Plan overview awaiting approval ────────────────────────
        if not plan_approved:
            back_triggers = ("2", "返回", "修改", "回", "back", "重新", "不要", "換")
            # Check "back" first so "換" / "修改" doesn't get swallowed by a later confirm match
            if low == "2" or any(tr in low for tr in ("返回", "修改", "重新", "不要", "換")):
                _do_back_to_phase0(t, back_triggers)
            elif low == "1" or any(tr in low for tr in ("確認", "ok", "可以", "是", "同意", "棒", "不錯", "好")):
                # Approve plan → move to hotel selection
                st.session_state[SESSION_MACRO_PLAN_APPROVED] = True
                st.session_state.pop(SESSION_DECISION_KEY, None)  # force next message injection
                st.session_state[SESSION_CHAT_HISTORY].append({
                    "role": "user",
                    "content": "✅ 確認行程架構，請幫我選住宿。",
                })
                st.rerun()
            else:
                st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                    "role": "assistant",
                    "content": (
                        "請輸入：\n"
                        "1. ✅ 確認行程架構，幫我找住宿\n"
                        "2. ↩️ 返回修改（可在「2」後面直接加上修改意見）"
                    ),
                })
                st.rerun()
            return

        # ── Step 2a: All hotels confirmed — await final proceed/back ──────
        if all_macro_confirmed:
            confirm_triggers = ("確認", "開始", "繼續", "go", "proceed", "好", "ok", "是", "1")
            back_triggers = ("返回", "修改", "回", "back", "重新", "2")
            if any(tr in low for tr in confirm_triggers):
                _apply_macro_hotels_to_workflow(workflow, macro_hotels)
                _resume_with_chat_update(
                    workflow,
                    UserDecision(choice=UserChoice.PROCEED_WITH_WARNINGS),
                    "確認行程計畫，開始細部規劃。",
                )
            elif any(tr in low for tr in back_triggers):
                _do_back_to_phase0(t, back_triggers)
            else:
                st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                    "role": "assistant",
                    "content": "所有住宿都已選定 🎉 請輸入「確認」開始細部規劃，或「返回」重新調整。",
                })
                st.rerun()
            return

        # ── Step 2b: Hotel selection for current city ─────────────────────
        current_city = cities[macro_step] if macro_step < len(cities) else cities[-1]
        macro = workflow.macro_plan
        seg = next((s for s in (macro.city_segments or []) if s.city == current_city), None)
        candidates: list[str] = getattr(seg, "hotel_candidates", []) if seg else []

        # "5" or vague = random selection; "4" = custom (needs actual name → fall through to text)
        if low in ("5",) or any(tr in low for tr in ("隨便", "都可以", "你決定", "幫我選", "random", "任何", "無所謂")):
            hotel_name = candidates[0] if candidates else None
        elif low == "4":
            # User wants to type their own — prompt them
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": f"請直接輸入您在「{current_city}」偏好的住宿名稱（例如：Hotel Monterey Osaka）",
            })
            st.rerun()
            return
        else:
            hotel_name = _interpret_macro_hotel_input(t, candidates)

        if hotel_name:
            _try_select_hotel_in_chat(
                settings, current_city, hotel_name, macro_step, cities, workflow, result
            )
        else:
            hint = f"請輸入 1~{len(candidates)}、4（自訂）或 5（隨便）" if candidates else "請輸入住宿名稱或說「隨便」"
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": f"抱歉，我沒有理解您的選擇。{hint}。",
            })
            st.rerun()
        return

    # ── AWAITING_DUPLICATE_DECISION ───────────────────────────────────────
    if status is WorkflowStatus.AWAITING_DUPLICATE_DECISION:
        conflict = result.conflict
        name = conflict.evidence.get("duplicate_place_name", "此地點") if conflict else "此地點"
        if low == "1" or any(tr in low for tr in ("是", "再去", "造訪", "沒關係", "再", "ok")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.ALLOW_REVISIT),
                f"好的，再次造訪「{name}」。",
            )
        elif low == "2" or any(tr in low for tr in ("換", "不去", "替換", "拒絕", "另一個", "不")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.REJECT_DUPLICATE),
                f"請幫我換一個景點，不要再安排「{name}」。",
            )
        else:
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": (
                    f"請問要再次造訪「{name}」嗎？\n"
                    "1. 是，再次造訪\n"
                    "2. 換一個新景點\n"
                    "（直接輸入數字或文字均可）"
                ),
            })
            st.rerun()
        return

    # ── AWAITING_PACE_DECISION ────────────────────────────────────────────
    if status is WorkflowStatus.AWAITING_PACE_DECISION:
        if low == "1" or any(tr in low for tr in ("重新", "換景點", "換", "調整景點", "replan")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.KEEP_PACE_REPLAN),
                "請維持原本的節奏，重新規劃今天的行程。",
            )
        elif low == "2" or any(tr in low for tr in ("接受", "沒關係", "ok", "好", "可以", "累點", "算了")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING),
                "沒關係，接受這天行程稍微緊湊。",
            )
        elif low == "3" or any(tr in low for tr in ("加快", "一般", "調整節奏", "提升", "一般模式")):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.INCREASE_DAY_PACE, new_pace=get_pace_profile(PaceLevel.STANDARD)),
                "請將今天的節奏調整為一般模式。",
            )
        else:
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": (
                    "請選擇調整方式：\n"
                    "1. 重新規劃（換景點，保持原節奏）\n"
                    "2. 接受（這天累一點沒關係）\n"
                    "3. 調整節奏（改為一般模式）\n"
                    "（直接輸入數字或文字均可）"
                ),
            })
            st.rerun()
        return

    # ── AWAITING_BUDGET_DECISION ──────────────────────────────────────────
    if status is WorkflowStatus.AWAITING_BUDGET_DECISION:
        import re as _re
        # Detect budget increase: "增加到5萬" / "預算改成6萬" / "加到50000" / "4 增加到5萬"
        _budget_increase_triggers = ("增加", "改成", "加到", "改到", "提高")
        _has_increase = any(tr in low for tr in _budget_increase_triggers)
        _new_budget: Decimal | None = None
        if _has_increase:
            _wan_match = _re.search(r"(\d+(?:\.\d+)?)\s*萬", t)
            _raw_match = _re.search(r"(\d{4,})", t)
            if _wan_match:
                _new_budget = Decimal(str(float(_wan_match.group(1)) * 10000))
            elif _raw_match:
                _new_budget = Decimal(_raw_match.group(1))

        # Numbered option matches (1/2/3/4 prefix)
        _digit = _re.match(r"^([1-4])\b", t.strip())
        _option = _digit.group(1) if _digit else None

        if _new_budget is not None and _new_budget >= workflow.trip_spec.budget_amount:
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.INCREASE_BUDGET_KEEP_PLAN, new_budget_limit=_new_budget),
                f"增加預算至 {int(_new_budget):,}，並保留目前行程。",
            )
        elif _option == "1" or any(tr in low for tr in ("接受", "ok", "好", "算了", "沒關係", "超支沒關係")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.ACCEPT_COST_WARNING),
                "接受可能超支的警告，繼續行程。",
            )
        elif _option == "2" or any(tr in low for tr in ("壓低", "減少", "省錢", "節省", "降低費用")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.KEEP_LOCKED_REDUCE_COST),
                "請保留必去景點，並壓低其他支出。",
            )
        elif _option == "3" or any(tr in low for tr in ("換景點", "更換", "替換", "維持預算")):
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.REPLACE_ITEMS_KEEP_BUDGET),
                "請維持預算，並更換部分景點。",
            )
        elif _option == "4":
            # Prompt user to type the new budget amount
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": "請輸入想提高到的預算，例如「增加到 5 萬」或「增加到 50000」",
            })
            st.rerun()
        else:
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": (
                    "請選擇調整方式：\n"
                    "1. 接受超支（繼續目前行程）\n"
                    "2. 壓低費用（保留必去景點，削減其他開銷）\n"
                    "3. 換景點（維持預算，更換部分景點）\n"
                    "4. 增加預算（輸入「4 增加到X萬」，例如「4 增加到5萬」）\n"
                    "（直接輸入數字或文字均可）"
                ),
            })
            st.rerun()
        return

    # ── AWAITING_PRICE_DECISION ───────────────────────────────────────────
    if status is WorkflowStatus.AWAITING_PRICE_DECISION:
        _resume_with_chat_update(
            workflow, UserDecision(choice=UserChoice.ACCEPT_COST_WARNING),
            "接受價格未確認的警告，繼續規劃。",
        )
        return

    # ── DAY_APPROVED ──────────────────────────────────────────────────────
    if status is WorkflowStatus.DAY_APPROVED:
        day = result.day_state.day
        total = workflow.trip_spec.total_days
        if _can_plan_next_day(current_day=day, total_days=total):
            approved = list(st.session_state.get(SESSION_APPROVED_DAYS, []))
            approved.append(result.day_state)
            st.session_state[SESSION_APPROVED_DAYS] = approved
            with st.status("⚙️ 多 Agent 團隊正在為您規劃中…", expanded=True) as _status:
                _pcb, _mcb = _make_planning_callbacks(_status)
                st.session_state[SESSION_RESULT] = workflow.start_day(
                    day + 1, progress_callback=_pcb, metrics_callback=_mcb
                )
            st.session_state.pop(SESSION_DECISION_KEY, None)
            st.rerun()
        return

    # ── NEEDS_MANUAL_REVIEW ───────────────────────────────────────────────
    if status is WorkflowStatus.NEEDS_MANUAL_REVIEW:
        back_triggers = ("2", "返回", "上一天", "回去", "back")
        day = result.day_state.day if result.day_state else 1
        prev_day_num = day - 1

        if low == "2" or any(tr in low for tr in ("返回", "上一天", "回去", "back")):
            approved = list(st.session_state.get(SESSION_APPROVED_DAYS, []))
            if prev_day_num >= 1 and len(approved) >= prev_day_num:
                prev_day_state = approved[prev_day_num - 1]
                trimmed = [d for d in approved if d.day < day]
                st.session_state[SESSION_APPROVED_DAYS] = trimmed
                st.session_state[SESSION_RESULT] = WorkflowResult(
                    status=WorkflowStatus.DAY_APPROVED, day_state=prev_day_state
                )
                st.session_state.pop(SESSION_DECISION_KEY, None)
                st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                    "role": "user", "content": f"↩️ 返回第 {prev_day_num} 天重新選擇",
                })
                st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                    "role": "assistant",
                    "content": f"好的，已返回第 {prev_day_num} 天。請輸入「下一天」或「繼續」開始規劃下一天。",
                })
                st.rerun()
            else:
                st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                    "role": "assistant",
                    "content": "目前已在第 1 天，無法返回上一天。請輸入「1」重新規劃。",
                })
                st.rerun()
        else:
            # "1" or any replan text → replan
            _resume_with_chat_update(
                workflow, UserDecision(choice=UserChoice.KEEP_PACE_REPLAN),
                "請幫我重新規劃，換一批不同的景點。",
            )
        return


def _render_macro_hotel_buttons(
    workflow: "TravelWorkflow",
    result: "WorkflowResult",
    settings: Settings,
    macro_step: int,
) -> None:
    """Render hotel-selection buttons for the current city (Phase 1 chat flow)."""
    _require_streamlit()
    macro = workflow.macro_plan
    cities = _get_cities_for_hotel_selection(workflow)
    macro_hotels: dict = st.session_state.get(SESSION_MACRO_HOTELS, {})
    all_confirmed = len(macro_hotels) >= len(cities)

    if all_confirmed:
        proceed_label = (
            "⚠️ 瞭解問題並開始細部規劃"
            if (result.conflict and result.conflict.reasons)
            else "✅ 確認計畫，開始細部規劃"
        )
        col_go, col_back = st.columns([2, 1])
        with col_go:
            if st.button(proceed_label, key="macro-proceed-chat", use_container_width=True):
                _apply_macro_hotels_to_workflow(workflow, macro_hotels)
                _resume_with_chat_update(
                    workflow,
                    UserDecision(choice=UserChoice.PROCEED_WITH_WARNINGS),
                    "確認行程計畫，開始細部規劃。",
                )
        with col_back:
            if st.button("← 返回修改", key="macro-back-chat", use_container_width=True):
                # Preserve chat history and collected params so the user keeps context
                for key in [
                    SESSION_WORKFLOW, SESSION_RESULT,
                    SESSION_MACRO_HOTELS, SESSION_MACRO_STEP, SESSION_DECISION_KEY,
                    SESSION_APPROVED_DAYS, "_macro_pins_populated",
                    SESSION_MACRO_PLAN_APPROVED,
                ]:
                    st.session_state.pop(key, None)
                st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                    "role": "assistant",
                    "content": "好的，我們重新調整您的計畫。請告訴我您想修改什麼？",
                })
                st.rerun()
        return

    current_city = cities[macro_step] if macro_step < len(cities) else cities[-1]
    seg = next((s for s in (macro.city_segments or []) if s.city == current_city), None)
    candidates: list[str] = getattr(seg, "hotel_candidates", []) if seg else []

    if candidates:
        descriptions: list[str] = getattr(seg, "hotel_descriptions", []) if seg else []
        cols = st.columns(min(len(candidates), 3))
        for i, hotel_name in enumerate(candidates[:3]):
            desc = descriptions[i] if i < len(descriptions) else ""
            if desc:
                cols[i].caption(desc)
            if cols[i].button(
                f"🏨 {hotel_name}",
                key=f"hotel-chat-{macro_step}-{i}",
                use_container_width=True,
            ):
                _try_select_hotel_in_chat(
                    settings, current_city, hotel_name, macro_step, cities, workflow, result
                )

    with st.expander("✏️ 輸入其他住宿名稱", expanded=not bool(candidates)):
        custom = st.text_input(
            "住宿名稱",
            key=f"hotel-custom-chat-{macro_step}",
            placeholder="e.g. Hotel Monterey Osaka",
            label_visibility="collapsed",
        )
        if st.button("確認自訂住宿", key=f"hotel-custom-confirm-chat-{macro_step}"):
            if custom.strip():
                _try_select_hotel_in_chat(
                    settings, current_city, custom.strip(), macro_step, cities, workflow, result
                )
            else:
                st.warning("請輸入住宿名稱。")


def _try_select_hotel_in_chat(
    settings: Settings,
    city: str,
    hotel_name: str,
    step: int,
    cities: list[str],
    workflow: "TravelWorkflow",
    result: "WorkflowResult",
) -> None:
    """Ground hotel, inject ack + optional next-city prompt, advance macro step."""
    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    try:
        hotel_stop = places_client.ground(hotel_name)
        hotel_stop = hotel_stop.model_copy(update={"locked": True})

        st.session_state[SESSION_CHAT_HISTORY].append(
            {"role": "user", "content": f"選擇住宿：**{hotel_name}**"}
        )
        st.session_state[SESSION_MACRO_HOTELS][city] = hotel_stop
        new_step = step + 1
        st.session_state[SESSION_MACRO_STEP] = new_step
        all_done = new_step >= len(cities)

        if all_done:
            ack = (
                f"✅ 已確認「{hotel_name}」作為 {city} 的住宿！"
                "所有城市住宿都選定了 🎉\n\n"
                "請輸入「確認」即可開始細部規劃，或輸入「返回」重新調整。"
            )
        else:
            next_city = cities[new_step]
            ack = (
                f"✅ 已確認「{hotel_name}」作為 {city} 的住宿！\n\n"
                f"接下來，請為「**{next_city}**」選擇住宿 👇"
            )

        st.session_state[SESSION_CHAT_HISTORY].append({"role": "assistant", "content": ack})
        # Update injection key so the render loop doesn't re-inject for this step
        new_key = _make_decision_key(result, new_step, all_done)
        st.session_state[SESSION_DECISION_KEY] = new_key
        st.rerun()

    except Exception:
        st.session_state[SESSION_CHAT_HISTORY].append({
            "role": "assistant",
            "content": (
                f"⚠️ 找不到「{hotel_name}」的地點資訊，"
                "請嘗試更完整的飯店名稱，或選擇其他選項。"
            ),
        })
        st.rerun()


def _render_duplicate_buttons(workflow: "TravelWorkflow", result: "WorkflowResult") -> None:
    _require_streamlit()
    conflict = result.conflict
    name = conflict.evidence.get("duplicate_place_name", "（未知地點）") if conflict else "（未知地點）"
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button(f"✅ 再次造訪「{name}」", key="dup-allow-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.ALLOW_REVISIT),
                f"好的，再次造訪「{name}」。",
            )
    with col_no:
        if st.button("🔄 換一個景點", key="dup-reject-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.REJECT_DUPLICATE),
                f"請幫我換一個景點，不要再安排「{name}」。",
            )


def _render_pace_buttons(workflow: "TravelWorkflow", result: "WorkflowResult") -> None:
    _require_streamlit()
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 維持節奏重新規劃", key="pace-replan-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.KEEP_PACE_REPLAN),
                "請維持原本的節奏，重新規劃今天的行程。",
            )
    with col2:
        if st.button("💪 接受這天較累", key="pace-accept-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.ACCEPT_PACE_WARNING),
                "沒關係，接受這天行程稍微緊湊。",
            )
    with col3:
        if st.button("⬆️ 改為一般模式", key="pace-raise-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(
                    choice=UserChoice.INCREASE_DAY_PACE,
                    new_pace=get_pace_profile(PaceLevel.STANDARD),
                ),
                "請將今天的節奏調整為一般模式。",
            )


def _render_budget_buttons(workflow: "TravelWorkflow", result: "WorkflowResult") -> None:
    _require_streamlit()
    new_budget = st.number_input(
        "新的預算上限（若要增加預算）",
        min_value=float(workflow.trip_spec.budget_amount),
        value=float(
            workflow.trip_spec.budget_override_history[-1]
            if workflow.trip_spec.budget_override_history
            else workflow.trip_spec.budget_amount
        ),
        step=1000.0,
        key="budget-new-chat",
    )
    c1, c2 = st.columns(2)
    c3, c4 = st.columns(2)
    with c1:
        if st.button("💳 增加預算並保留行程", key="budget-increase-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(
                    choice=UserChoice.INCREASE_BUDGET_KEEP_PLAN,
                    new_budget_limit=Decimal(str(new_budget)),
                ),
                f"增加預算至 {new_budget:,.0f}，並保留目前行程。",
            )
    with c2:
        if st.button("✂️ 壓低其他支出", key="budget-reduce-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.KEEP_LOCKED_REDUCE_COST),
                "請保留必去景點，並壓低其他支出。",
            )
    with c3:
        if st.button("🔄 維持預算允許換景點", key="budget-replace-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.REPLACE_ITEMS_KEEP_BUDGET),
                "請維持預算，並更換部分景點。",
            )
    with c4:
        if st.button("⚠️ 接受超支警告", key="budget-accept-chat", use_container_width=True):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.ACCEPT_COST_WARNING),
                "接受可能超支的警告，繼續行程。",
            )


def _render_price_buttons(workflow: "TravelWorkflow", result: "WorkflowResult") -> None:
    _require_streamlit()
    if st.button("✅ 接受未確認價格，繼續規劃", key="price-accept-chat", use_container_width=True):
        _resume_with_chat_update(
            workflow,
            UserDecision(choice=UserChoice.ACCEPT_COST_WARNING),
            "接受價格未確認的警告，繼續規劃。",
        )


def _render_manual_review_buttons(
    workflow: "TravelWorkflow",
    result: "WorkflowResult",
) -> None:
    """Recovery actions shown when a day lands in NEEDS_MANUAL_REVIEW."""
    _require_streamlit()
    day = result.day_state.day if result.day_state else 1

    col_replan, col_back = st.columns(2)
    with col_replan:
        if st.button(
            "🔄 重新規劃此日（換一批景點）",
            key=f"manual-replan-{day}",
            use_container_width=True,
        ):
            _resume_with_chat_update(
                workflow,
                UserDecision(choice=UserChoice.KEEP_PACE_REPLAN),
                f"請幫我重新規劃第 {day} 天，換一批不同的景點。",
            )

    with col_back:
        approved = list(st.session_state.get(SESSION_APPROVED_DAYS, []))
        prev_day = day - 1
        can_go_back = prev_day >= 1 and len(approved) >= prev_day
        if st.button(
            f"↩️ 回到第 {prev_day} 天重新選擇" if can_go_back else "↩️ 返回上一天",
            key=f"manual-goback-{day}",
            use_container_width=True,
            disabled=not can_go_back,
        ):
            # Restore the previous day's approved state without re-running the workflow
            prev_day_state = approved[prev_day - 1]
            # Remove current failed day from approved list (it was never added, but trim in case)
            trimmed = [d for d in approved if d.day < day]
            st.session_state[SESSION_APPROVED_DAYS] = trimmed
            st.session_state[SESSION_RESULT] = WorkflowResult(
                status=WorkflowStatus.DAY_APPROVED,
                day_state=prev_day_state,
            )
            st.session_state.pop(SESSION_DECISION_KEY, None)
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "user",
                "content": f"↩️ 返回第 {prev_day} 天重新選擇",
            })
            st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append({
                "role": "assistant",
                "content": (
                    f"好的，已返回第 {prev_day} 天。"
                    "請輸入「下一天」或「繼續」重新規劃下一天。"
                ),
            })
            st.rerun()


def _render_approved_buttons(
    workflow: "TravelWorkflow",
    result: "WorkflowResult",
    settings: Settings,  # noqa: ARG001 – kept for signature consistency
) -> None:
    _require_streamlit()
    day = result.day_state.day
    total = workflow.trip_spec.total_days

    if _can_plan_next_day(current_day=day, total_days=total):
        next_day = day + 1
        if st.button(
            f"▶️ 開始規劃第 {next_day} 天",
            key=f"plan-day-{next_day}-chat",
            use_container_width=True,
        ):
            approved = list(st.session_state.get(SESSION_APPROVED_DAYS, []))
            approved.append(result.day_state)
            st.session_state[SESSION_APPROVED_DAYS] = approved
            st.session_state[SESSION_RESULT] = workflow.start_day(next_day)
            st.session_state.pop(SESSION_DECISION_KEY, None)
            st.rerun()
    else:
        _render_trip_summary(workflow)


def _make_metrics_cb():
    """Return a (agent_name, in_tokens, out_tokens, elapsed_s) callback that accumulates
    per-agent stats in session_state and live-redraws the sidebar table.

    Safe to call outside a ``st.status`` context (e.g. validate_macro, intent parser).
    The updater closures are set in main() and refreshed each render cycle.
    """
    _upd = st.session_state.get(SESSION_SIDEBAR_UPDATERS, {})
    _upd_redraw = _upd.get("redraw")

    def _cb(agent_name: str, in_tokens: int, out_tokens: int, elapsed_s: float) -> None:
        _m = st.session_state.setdefault(SESSION_METRICS, _fresh_metrics())
        if agent_name in _m:
            _entry = _m[agent_name]
            _entry["status"] = "💤 Idle"   # just finished — reset to idle
            _entry["in_tokens"] += in_tokens
            _entry["out_tokens"] += out_tokens
            _entry["time_s"] = round(_entry["time_s"] + elapsed_s, 2)
        if _upd_redraw is not None:
            _upd_redraw(_m)

    return _cb


def _make_planning_callbacks(status):
    """Return (progress_cb, metrics_cb) for a workflow step running inside ``st.status``.

    - ``progress_cb(message)``  → writes to the status panel, updates the sidebar current-
      action caption, and flips the matching agent's status to 🟢 Running in the table.
    - ``metrics_cb(name, in_tok, out_tok, s)`` → accumulates per-agent stats, resets status
      to 💤 Idle, and redraws the sidebar table via the render-cycle updater closures.
    """
    _upd = st.session_state.get(SESSION_SIDEBAR_UPDATERS, {})
    _upd_current = _upd.get("current")
    _upd_redraw = _upd.get("redraw")
    _mcb = _make_metrics_cb()

    def _progress_cb(message: str) -> None:
        status.write(message)
        if _upd_current is not None:
            _upd_current(message)
        # Flip the matching agent to 🟢 Running in the metrics table
        _m = st.session_state.get(SESSION_METRICS)
        if _m is not None:
            for _substr, _eng_name in _EMIT_TO_AGENT.items():
                if _substr in message and _eng_name in _m:
                    # Clear any previous running marker first
                    for _entry in _m.values():
                        if _entry["status"] == "🟢 Running":
                            _entry["status"] = "💤 Idle"
                    _m[_eng_name]["status"] = "🟢 Running"
                    break
            if _upd_redraw is not None:
                _upd_redraw(_m)

    return _progress_cb, _mcb


def _resume_with_chat_update(
    workflow: "TravelWorkflow",
    decision: UserDecision,
    user_label: str,
) -> None:
    """Append the user's choice to chat history, resume workflow, clear injection key."""
    _require_streamlit()
    st.session_state.setdefault(SESSION_CHAT_HISTORY, []).append(
        {"role": "user", "content": user_label}
    )
    with st.status("⚙️ 多 Agent 團隊正在為您規劃中…", expanded=True) as _status:
        _pcb, _mcb = _make_planning_callbacks(_status)
        st.session_state[SESSION_RESULT] = workflow.resume(
            decision, progress_callback=_pcb, metrics_callback=_mcb
        )
    st.session_state.pop(SESSION_DECISION_KEY, None)
    st.rerun()


def _render_completed_days_summary(approved_days: list) -> None:
    _require_streamlit()
    with st.expander(f"已完成 {len(approved_days)} 天行程", expanded=False):
        # ── Itinerary table ───────────────────────────────────────────────
        table_rows = []
        for day_state in approved_days:
            table_rows.append({
                "天次": f"Day {day_state.day}",
                "城市": day_state.destination or "—",
                "景點": "、".join(p.name for p in day_state.places) or "—",
                "餐廳": "、".join(m.name for m in day_state.meals) or "—",
                "移動時間": (
                    f"{day_state.route.total_required_transfer_minutes} 分"
                    if day_state.route else "—"
                ),
                "距離": (
                    f"{getattr(day_state.route, 'total_distance_km', 0.0):.1f} km（步行 {day_state.route.walking_distance_km:.1f} km）"
                    if day_state.route else "—"
                ),
                "品質": (
                    f"{day_state.quality_score}/5"
                    if day_state.quality_score is not None else "—"
                ),
            })
        st.dataframe(table_rows, use_container_width=True, hide_index=True)


def _render_trip_summary(workflow: TravelWorkflow) -> None:
    _require_streamlit()
    st.success(f"全部 {workflow.trip_spec.total_days} 天行程已規劃完成。")
    all_approved = workflow.approved_days
    if not all_approved:
        return

    # PDF download — placed immediately under the completion banner
    try:
        _pdf_bytes = generate_itinerary_pdf(workflow.trip_spec, all_approved)
        _pdf_dest = workflow.trip_spec.destination.replace(" ", "_")
        _pdf_days = workflow.trip_spec.total_days
        st.download_button(
            label="📄 下載完整行程 PDF",
            data=_pdf_bytes,
            file_name=f"{_pdf_dest}_{_pdf_days}天行程.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as _pdf_err:  # noqa: BLE001
        st.warning(f"PDF 產生失敗，請稍後再試。（{_pdf_err}）")

    st.subheader("旅程總覽")
    all_prices = list(workflow.trip_spec.prices)
    for day_state in all_approved:
        all_prices.extend(day_state.prices)
    fx = workflow.trip_spec.fx_snapshot
    confirmed_total = (
        sum(
            (p.amount_original or Decimal("0")) * fx.rate
            for p in all_prices
            if p.amount_original is not None and p.status is not PriceStatus.MISSING_PRICE
        ) or Decimal("0")
    ).quantize(Decimal("0.01")) if fx else None

    all_places = [
        place.name
        for day_state in all_approved
        for place in day_state.places
    ]
    if all_places:
        st.write("全程景點：" + "、".join(all_places))
    if confirmed_total is not None:
        st.metric(
            f"已確認費用（{workflow.trip_spec.budget_currency}）",
            str(confirmed_total),
        )


def _build_live_workflow(settings: Settings, trip_spec: TripSpec) -> TravelWorkflow:
    tracer = LangfuseTracer() if settings.langfuse_enabled else NoOpTracer()
    agents = LiveAgentSuite(settings)
    return TravelWorkflow(
        trip_spec=trip_spec,
        agents=agents,
        places=GooglePlacesClient(settings.google_maps_api_key.get_secret_value()),
        routes=GoogleRoutesClient(settings.google_maps_api_key.get_secret_value()),
        price_collector=LivePriceCollector(),
        reviewer=agents,
        tracer=tracer,
    )


def _build_trip_spec_from_preflight(
    *,
    settings: Settings,
    destination: str,
    days: int,
    budget_amount: str,
    budget_currency: str,
    interests: str,
    pace_level: PaceLevel,
    route_mode: RouteMode,
    walking_preference: WalkingPreference,
    grounded_must_visit: list[PlaceStop],
    must_visit_name: str,
    must_visit_price: str,
    must_visit_price_url: str,
    extra_legs: list[dict] | None = None,
    user_constraints: list[str] | None = None,
) -> TripSpec:
    """Build a TripSpec with a placeholder hotel.

    Hotels are selected during the Phase 1 macro confirmation step;
    ``_apply_macro_hotels_to_workflow`` updates them before micro-planning.
    """
    destination = destination.strip()
    must_visit_name = must_visit_name.strip()
    must_visit_price = must_visit_price.strip()
    _validate_trip_submission_inputs(
        destination=destination,
        days=days,
        budget_amount=budget_amount,
        must_visit_name=must_visit_name,
        must_visit_price=must_visit_price,
    )

    rates_client = ExchangeRateClient(settings.exchange_rate_api_key.get_secret_value())
    fx_snapshot = rates_client.snapshot(base_currency="JPY", target_currency=budget_currency)
    trip_prices: list[PriceRecord] = []
    must_visit = [stop.model_copy(update={"locked": True}) for stop in grounded_must_visit]
    if must_visit_price and must_visit:
        trip_prices.append(
            PriceRecord(
                item_id="must-visit-1",
                item_name=must_visit[0].name,
                category="admission",
                amount_original=Decimal(must_visit_price),
                currency_original="JPY",
                status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
                source_provider="User Confirmed Official Source",
                source_url=must_visit_price_url or None,
            )
        )

    # Hotel placeholder — replaced during macro confirmation step
    legs: list[DestinationLeg] = []
    if extra_legs:
        legs.append(
            DestinationLeg(
                city=destination,
                num_days=days,
                hotel=_PLACEHOLDER_HOTEL,
                must_visit=must_visit,
            )
        )
        for leg_data in extra_legs:
            legs.append(
                DestinationLeg(
                    city=leg_data["city"],
                    num_days=leg_data["num_days"],
                    hotel=_PLACEHOLDER_HOTEL,
                    must_visit=leg_data["must_visit"],
                )
            )

    return TripSpec(
        destination=destination,
        days=days,
        budget_amount=Decimal(budget_amount),
        budget_currency=budget_currency,
        interests=[part.strip() for part in interests.split(",") if part.strip()],
        pace=get_pace_profile(pace_level),
        route_mode=route_mode,
        walking_preference=walking_preference,
        hotel=_PLACEHOLDER_HOTEL,
        must_visit=must_visit,
        prices=trip_prices,
        fx_snapshot=ExchangeRateSnapshot.model_validate(fx_snapshot.model_dump()),
        legs=legs,
        user_constraints=user_constraints or [],
    )



def _validate_trip_submission_inputs(
    *,
    destination: str,
    days: int,
    budget_amount: str,
    must_visit_name: str,
    must_visit_price: str,
) -> None:
    if not destination.strip():
        raise ValueError("目的地不能空白")
    if must_visit_price.strip() and not must_visit_name.strip():
        raise ValueError("填寫必去景點價格前，請先輸入必去景點名稱")
    coerce_range_value(days, DAYS_SPEC)
    coerce_range_value(budget_amount, TOTAL_BUDGET_SPEC)


def _build_must_visit_preview_queries(raw_text: str) -> list[str]:
    normalized = raw_text.replace("\n", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _preview_destination(settings: Settings, destination: str) -> PlaceStop | None:
    if not destination.strip():
        return None
    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    try:
        return places_client.lookup_destination(destination.strip())
    except (GroundingNotFound, httpx.HTTPStatusError):
        return None



def _preview_must_visit_stops(settings: Settings, raw_text: str) -> tuple[list[PlaceStop], list[str]]:
    queries = _build_must_visit_preview_queries(raw_text)
    if not queries:
        return [], []

    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    successful: list[PlaceStop] = []
    errors: list[str] = []
    for query in queries:
        try:
            successful.append(_ground_required_place(places_client, query, field_label="必去景點"))
        except ValueError as error:
            errors.append(str(error))
    return successful, errors



def _get_cities_for_hotel_selection(workflow: TravelWorkflow) -> list[str]:
    """Return ordered list of cities that need a hotel selected during macro phase."""
    if workflow.trip_spec.legs:
        return [leg.city for leg in workflow.trip_spec.legs]
    return [workflow.trip_spec.destination]


def _apply_macro_hotels_to_workflow(
    workflow: TravelWorkflow, macro_hotels: dict
) -> None:
    """Inject user-selected hotels into the workflow's TripSpec before micro-planning."""
    if workflow.trip_spec.legs:
        for leg in workflow.trip_spec.legs:
            if leg.city in macro_hotels:
                leg.hotel = macro_hotels[leg.city]
        # Keep top-level hotel in sync with first leg
        if workflow.trip_spec.legs[0].city in macro_hotels:
            workflow.trip_spec.hotel = macro_hotels[workflow.trip_spec.legs[0].city]
    else:
        dest = workflow.trip_spec.destination
        if dest in macro_hotels:
            workflow.trip_spec.hotel = macro_hotels[dest]


def _render_city_overview_card(seg: object, confirmed_hotel: "PlaceStop | None") -> None:
    """Render a single city card with overview info and hotel confirmation status."""
    _require_streamlit()
    if confirmed_hotel:
        header_color = "#1a4731"
        hotel_line = f"🏨 {confirmed_hotel.name} ✓"
        hotel_color = "#86efac"
    else:
        header_color = "#1e3a5f"
        hotel_line = "🏨 住宿待確認…"
        hotel_color = "#93c5fd"

    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, {header_color} 0%, {header_color}bb 100%);
            border-radius: 12px;
            padding: 1.1rem 1rem 0.9rem;
            margin-bottom: 0.4rem;
        ">
            <h3 style="margin:0 0 0.15rem; color:#fff; font-size:1.1rem;">
                🏙️ {seg.city}
            </h3>
            <p style="margin:0; color:#93c5fd; font-size:0.82rem;">{seg.day_range}</p>
            <p style="margin:0.35rem 0 0; color:{hotel_color}; font-size:0.82rem;">
                {hotel_line}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if seg.theme:
        st.markdown(f"**主題** ｜ {seg.theme}")
    if seg.key_places:
        st.markdown("📍 **精選景點**")
        for place in seg.key_places:
            st.markdown(f"&nbsp;&nbsp;&nbsp;• {place}")
    if seg.food_picks:
        st.markdown("🍜 **必嚐美食**")
        for food in seg.food_picks:
            st.markdown(f"&nbsp;&nbsp;&nbsp;• {food}")
    if seg.notes:
        st.info(seg.notes, icon="💡")


def _render_hotel_selection_step(
    *,
    settings: Settings,
    city: str,
    seg: object,
    step: int,
    total: int,
) -> None:
    """Render the hotel-selection UI for one city in the guided macro discussion."""
    _require_streamlit()
    st.markdown(f"### 🏨 請為 **{city}** 選擇住宿（{step + 1} / {total}）")

    # Show error from previous attempt (if any)
    err_key = f"hotel-err-{step}"
    if err_key in st.session_state:
        st.error(st.session_state.pop(err_key))

    candidates: list[str] = getattr(seg, "hotel_candidates", []) if seg else []

    if candidates:
        st.write("**AI 推薦住宿**（點選後系統自動驗證地點）：")
        for i, hotel_name in enumerate(candidates[:3], 1):
            if st.button(
                f"{i}. {hotel_name}",
                key=f"hotel-btn-{step}-{i}",
                use_container_width=True,
            ):
                _try_select_hotel(settings, city, hotel_name, step, total, err_key)
        st.markdown("---")

    label = "🔍 自行輸入住宿名稱" if candidates else "🔍 輸入住宿名稱"
    custom = st.text_input(
        label,
        key=f"hotel-custom-{step}",
        placeholder="e.g. Hotel Granvia Kyoto",
    )
    if st.button("確認自訂住宿", key=f"hotel-custom-confirm-{step}"):
        if custom.strip():
            _try_select_hotel(settings, city, custom.strip(), step, total, err_key)
        else:
            st.session_state[err_key] = "請輸入住宿名稱"
            st.rerun()


def _try_select_hotel(
    settings: Settings,
    city: str,
    hotel_name: str,
    step: int,
    total: int,
    err_key: str,
) -> None:
    """Ground ``hotel_name`` via Google Places and advance the macro hotel-selection step."""
    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    try:
        hotel_stop = places_client.ground(hotel_name)
        hotel_stop = hotel_stop.model_copy(update={"locked": True})
        st.session_state[SESSION_MACRO_HOTELS][city] = hotel_stop
        st.session_state[SESSION_MACRO_STEP] = step + 1
        st.rerun()
    except (GroundingNotFound, httpx.HTTPStatusError):
        st.session_state[err_key] = (
            f"找不到「{hotel_name}」，請嘗試其他選項或使用更完整的名稱。"
        )
        st.rerun()


def _ground_extra_legs(settings: Settings, raw_list: list[dict]) -> list[dict]:
    """Ground hotels and must-visit stops for each extra leg in ``raw_list``.

    Returns a list of dicts ready for ``_build_trip_spec_from_preflight``::

        {"city": str, "num_days": int, "hotel": PlaceStop, "must_visit": list[PlaceStop]}

    Raises ``ValueError`` if a required field (city / hotel) is missing or un-groundable.
    """
    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    result: list[dict] = []
    for i, leg in enumerate(raw_list):
        city = leg["city"]
        hotel_name = leg["hotel_name"]
        city_label = f"城市 {i + 2}"
        if not city:
            raise ValueError(f"{city_label} 名稱不能空白")
        if hotel_name:
            hotel = _ground_required_place(places_client, hotel_name, field_label=f"{city_label}住宿")
        else:
            # Hotel will be selected during the macro confirmation phase.
            hotel = _PLACEHOLDER_HOTEL
        must_visit_stops, _ = _preview_must_visit_stops(
            settings, leg.get("must_visit_raw", "")
        )
        result.append({
            "city": city,
            "num_days": leg["num_days"],
            "hotel": hotel,
            "must_visit": [stop.model_copy(update={"locked": True}) for stop in must_visit_stops],
        })
    return result


def _ground_required_place(places_client: GooglePlacesClient, query: str, *, field_label: str) -> PlaceStop:
    try:
        return places_client.ground(query)
    except (GroundingNotFound, httpx.HTTPStatusError) as error:
        raise ValueError(_build_user_input_error(error, field_label=field_label, query=query)) from error


def _build_user_input_error(error: Exception, *, field_label: str, query: str) -> str:
    if isinstance(error, GroundingNotFound):
        return f"{field_label}「{query}」找不到可驗證地點，請改用更完整或正式的名稱。"
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 400:
        return f"{field_label}「{query}」格式無效，請改用更完整或正式的名稱。"
    return f"{field_label}「{query}」目前無法完成地點驗證，請稍後重試。"


def _format_converted_price(price: PriceRecord, fx: ExchangeRateSnapshot) -> str:
    if price.amount_original is not None:
        return str((price.amount_original * fx.rate).quantize(Decimal("0.01")))
    if price.amount_original_max is not None:
        return str((price.amount_original_max * fx.rate).quantize(Decimal("0.01")))
    return "N/A"


def _format_original_price(price: PriceRecord) -> str:
    if price.amount_original is not None:
        return f"{price.currency_original} {price.amount_original}"
    if price.amount_original_min is not None and price.amount_original_max is not None:
        return f"{price.currency_original} {price.amount_original_min}-{price.amount_original_max}"
    return f"{price.currency_original} 待確認"


def _pace_label_for_limit(limit_minutes: int) -> str:
    for level in PaceLevel:
        profile = get_pace_profile(level)
        if profile.max_required_transfer_minutes_per_day == limit_minutes:
            return PACE_LABELS[level]
    return "所選"


def _resume_workflow(workflow: TravelWorkflow, decision: UserDecision) -> None:
    with st.status("⚙️ 多 Agent 團隊正在為您規劃中…", expanded=True) as _status:
        _pcb, _mcb = _make_planning_callbacks(_status)
        st.session_state[SESSION_RESULT] = workflow.resume(
            decision, progress_callback=_pcb, metrics_callback=_mcb
        )
    st.rerun()


def _require_streamlit() -> None:
    if st is None:
        raise ModuleNotFoundError("streamlit is required to render the UI")


if __name__ == "__main__":
    main()
