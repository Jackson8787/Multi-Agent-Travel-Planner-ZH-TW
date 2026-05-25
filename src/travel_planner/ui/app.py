from decimal import Decimal, InvalidOperation

import httpx
from pydantic import ValidationError

try:  # pragma: no cover - exercised only when streamlit is installed
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover - import fallback for unit tests
    st = None

from travel_planner.agents.runner import LiveAgentSuite
from travel_planner.config import Settings
from travel_planner.domain.models import (
    ConflictType,
    DayPlanState,
    ExchangeRateSnapshot,
    PlaceStop,
    PriceRecord,
    PriceStatus,
    RouteMode,
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
SESSION_HOTEL_CANDIDATES = "hotel_candidates"
SESSION_SELECTED_HOTEL = "selected_hotel"
SESSION_MUST_VISIT_STOPS = "must_visit_stops"
SESSION_MUST_VISIT_ERRORS = "must_visit_errors"
SESSION_MANUAL_HOTEL = "manual_hotel"
SESSION_MANUAL_HOTEL_ERROR = "manual_hotel_error"

DAYS_SPEC = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)
TOTAL_BUDGET_SPEC = RangeFieldSpec(label="總預算", minimum=1000, maximum=300000, step=1000, unit="NTD")
LODGING_BUDGET_SPEC = RangeFieldSpec(label="住宿預算", minimum=0, maximum=150000, step=1000, unit="NTD")

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
    warnings = set(result.day_state.warnings)
    if "ROUTE_UNAVAILABLE" in warnings:
        return "路線驗證連續失敗兩次，系統無法算出完整交通路線。請調整住宿、景點組合或稍後重試。"
    if "GROUNDING_FAILED" in warnings:
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
    st.markdown(
        """
        <style>
        @media (min-width: 1024px) {
          div[data-testid="stColumn"]:has(#sticky-map-anchor) {
            position: sticky;
            top: 1rem;
            align-self: flex-start;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    try:
        settings = Settings()
    except (ValidationError, ValueError, InvalidOperation) as error:
        st.session_state[SESSION_ERROR] = str(error)
        return
    except Exception as error:  # pragma: no cover - UI fallback
        st.session_state[SESSION_ERROR] = f"初始化 live workflow 失敗：{error}"
        return

    left, right = st.columns([1, 1.2])
    with left:
        destination = st.text_input("目的地", value="Osaka")
        days = _render_synced_range_input(
            "旅遊天數",
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
        lodging_budget_amount = _render_synced_range_input(
            "住宿預算",
            spec=LODGING_BUDGET_SPEC,
            slider_key="budget-lodging-slider",
            input_key="budget-lodging-input",
            default=8000,
        )
        st.caption(f"目前住宿預算：NTD {int(lodging_budget_amount):,}")

        destination_stop = _preview_destination(settings, destination)
        hotel_candidates = _preview_hotel_candidates(settings, destination)
        candidate_hotel = _render_hotel_candidates(hotel_candidates)
        manual_hotel_name = st.text_input("自行指定住宿地點", value="")
        manual_hotel, manual_hotel_error = _preview_manual_hotel(settings, manual_hotel_name)

        budget_currency = st.selectbox("預算幣別", options=["TWD"], index=0)
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
        must_visit_name = st.text_area("必去景點", value="Universal Studios Japan")
        must_visit_price = st.text_input("必去景點價格 (JPY)", value="8600")
        must_visit_price_url = st.text_input("必去景點官方價格 URL")
        pace_level = st.radio(
            "旅遊節奏",
            options=list(PaceLevel),
            format_func=lambda value: f"{PACE_LABELS[value]}: {PACE_DESCRIPTIONS[value]}",
            index=1,
        )

        must_visit_stops, must_visit_errors = _preview_must_visit_stops(settings, must_visit_name)
        selected_hotel = _select_effective_hotel(candidate_hotel=candidate_hotel, manual_hotel=manual_hotel)
        preview_hotels = _merge_preview_hotels(hotel_candidates, manual_hotel)

        st.session_state[SESSION_DESTINATION_STOP] = destination_stop
        st.session_state[SESSION_HOTEL_CANDIDATES] = hotel_candidates
        st.session_state[SESSION_MUST_VISIT_STOPS] = must_visit_stops
        st.session_state[SESSION_MUST_VISIT_ERRORS] = must_visit_errors
        st.session_state[SESSION_MANUAL_HOTEL] = manual_hotel
        st.session_state[SESSION_MANUAL_HOTEL_ERROR] = manual_hotel_error

        if manual_hotel is not None:
            st.success(f"目前使用手動指定住宿：{manual_hotel.name}")
        elif selected_hotel is not None:
            st.success(f"已選住宿：{selected_hotel.name}")
        else:
            st.warning("請先從住宿候補中選擇一間住宿。")

        submitted = st.button("開始規劃", disabled=selected_hotel is None)

    with right:
        st.markdown("<div id='sticky-map-anchor'></div>", unsafe_allow_html=True)
        st.subheader("地圖預覽")
        render_preview_map(
            settings.google_maps_api_key.get_secret_value(),
            destination_stop=destination_stop,
            hotel_candidates=preview_hotels,
            must_visit_stops=must_visit_stops,
            selected_hotel_place_id=selected_hotel.place_id if selected_hotel else None,
        )
        if destination_stop is None and not preview_hotels and not must_visit_stops:
            st.info("輸入目的地後，這裡會顯示城市、住宿候補與必去景點預覽。")
        if manual_hotel_error:
            st.warning(manual_hotel_error)
        for warning in must_visit_errors:
            st.warning(warning)

    if not submitted:
        return

    try:
        _validate_trip_submission_inputs(
            destination=destination,
            days=int(days),
            budget_amount=str(budget_amount),
            lodging_budget_amount=str(lodging_budget_amount),
            selected_hotel_place_id=selected_hotel.place_id if selected_hotel else None,
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
        )
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
            selected_hotel=selected_hotel,
            grounded_must_visit=must_visit_stops,
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
            must_visit_price_url=must_visit_price_url,
        )
    except (ValidationError, ValueError, InvalidOperation) as error:
        st.session_state[SESSION_ERROR] = str(error)
        return

    workflow = _build_live_workflow(settings, trip_spec)
    st.session_state[SESSION_SETTINGS] = settings
    st.session_state[SESSION_WORKFLOW] = workflow
    st.session_state[SESSION_RESULT] = workflow.start_day(1)
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
        extra.metric("步行距離", f"{route.walking_distance_km:.1f} km")
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
        observed = int(result.day_state.route.total_required_transfer_minutes) if result.day_state.route else 0
        limit = workflow.trip_spec.pace.max_required_transfer_minutes_per_day
        st.write(format_pace_conflict(observed, limit))
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


def render_approved_itinerary(settings: Settings, workflow: TravelWorkflow, result: WorkflowResult) -> None:
    _require_streamlit()
    st.header("已核准日程")
    route = result.day_state.route
    fx = workflow.trip_spec.fx_snapshot
    if route is not None:
        left, center, right = st.columns(3)
        left.metric("必要移動", f"{route.total_required_transfer_minutes} 分")
        center.metric("最長單段", f"{route.max_single_transfer_minutes} 分")
        right.metric("步行距離", f"{route.walking_distance_km:.1f} km")
        st.caption(
            f"交通方式：{ROUTE_MODE_LABELS[workflow.trip_spec.route_mode]} | "
            f"步行偏好：{WALKING_PREFERENCE_LABELS[workflow.trip_spec.walking_preference]}"
        )
        if "drive fallback" in route.source_provider:
            st.warning("此段無可取得的大眾運輸路線，已改用駕車估算。路線時間僅供參考，實際通勤時間請另行確認。")

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

    render_verified_route_map(
        settings.google_maps_api_key.get_secret_value(),
        [workflow.trip_spec.hotel] + result.day_state.places + result.day_state.meals,
        route.encoded_polyline if route else None,
        route.encoded_polyline_segments if route else None,
    )

    if _can_plan_next_day(current_day=result.day_state.day, total_days=workflow.trip_spec.days):
        next_day = result.day_state.day + 1
        if st.button(f"規劃第 {next_day} 天", key=f"plan-day-{next_day}"):
            st.session_state[SESSION_RESULT] = workflow.start_day(next_day)
            st.rerun()
    else:
        st.success("全部天數已規劃完成。")


def main() -> None:
    _require_streamlit()
    st.set_page_config(page_title="Multi-Agent Travel Planner", layout="wide")
    st.title("Multi-Agent Travel Planner")
    st.caption("Superpowers-guided verification workflow with live provider evidence.")

    error = st.session_state.get(SESSION_ERROR)
    if error:
        st.error(error)

    workflow = st.session_state.get(SESSION_WORKFLOW)
    result = st.session_state.get(SESSION_RESULT)
    settings = st.session_state.get(SESSION_SETTINGS)

    if workflow is None or result is None or settings is None:
        render_trip_spec_form()
        return

    render_running_or_evidence(workflow, result)
    if result.status in {
        WorkflowStatus.AWAITING_PACE_DECISION,
        WorkflowStatus.AWAITING_PRICE_DECISION,
        WorkflowStatus.AWAITING_BUDGET_DECISION,
    }:
        render_decision_gate(workflow, result)
        return

    if result.status is WorkflowStatus.DAY_APPROVED:
        render_approved_itinerary(settings, workflow, result)
        return

    if result.status is WorkflowStatus.NEEDS_MANUAL_REVIEW:
        st.error("此日行程需要人工處理。")
        st.write(_format_manual_review_reason(result))


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
    selected_hotel: PlaceStop,
    grounded_must_visit: list[PlaceStop],
    must_visit_name: str,
    must_visit_price: str,
    must_visit_price_url: str,
) -> TripSpec:
    destination = destination.strip()
    must_visit_name = must_visit_name.strip()
    must_visit_price = must_visit_price.strip()
    _validate_trip_submission_inputs(
        destination=destination,
        days=days,
        budget_amount=budget_amount,
        lodging_budget_amount="1",
        selected_hotel_place_id=selected_hotel.place_id,
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

    return TripSpec(
        destination=destination,
        days=days,
        budget_amount=Decimal(budget_amount),
        budget_currency=budget_currency,
        interests=[part.strip() for part in interests.split(",") if part.strip()],
        pace=get_pace_profile(pace_level),
        route_mode=route_mode,
        walking_preference=walking_preference,
        hotel=selected_hotel,
        must_visit=must_visit,
        prices=trip_prices,
        fx_snapshot=ExchangeRateSnapshot.model_validate(fx_snapshot.model_dump()),
    )


def _validate_trip_spec_inputs(
    *,
    destination: str,
    hotel_name: str,
    must_visit_name: str,
    must_visit_price: str,
) -> None:
    if not destination.strip():
        raise ValueError("目的地不能空白")
    if not hotel_name.strip():
        raise ValueError("住宿名稱不能空白")
    if must_visit_price.strip() and not must_visit_name.strip():
        raise ValueError("填寫必去景點價格前，請先輸入必去景點名稱")


def _validate_trip_submission_inputs(
    *,
    destination: str,
    days: int,
    budget_amount: str,
    lodging_budget_amount: str,
    selected_hotel_place_id: str | None,
    must_visit_name: str,
    must_visit_price: str,
) -> None:
    _validate_trip_spec_inputs(
        destination=destination,
        hotel_name="selected-hotel",
        must_visit_name=must_visit_name,
        must_visit_price=must_visit_price,
    )
    coerce_range_value(days, DAYS_SPEC)
    coerce_range_value(budget_amount, TOTAL_BUDGET_SPEC)
    coerce_range_value(lodging_budget_amount, LODGING_BUDGET_SPEC)
    if not selected_hotel_place_id:
        raise ValueError("請先從住宿候補中選擇一間住宿。")


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


def _preview_hotel_candidates(settings: Settings, destination: str) -> list[PlaceStop]:
    if not destination.strip():
        return []
    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    try:
        return places_client.search_hotel_candidates(destination.strip())
    except (GroundingNotFound, httpx.HTTPStatusError):
        return []


def _preview_manual_hotel(settings: Settings, raw_text: str) -> tuple[PlaceStop | None, str | None]:
    if not raw_text.strip():
        return None, None
    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    try:
        return _ground_required_place(places_client, raw_text.strip(), field_label="自行指定住宿地點"), None
    except ValueError as error:
        return None, str(error)


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


def _render_hotel_candidates(candidates: list[PlaceStop]) -> PlaceStop | None:
    _require_streamlit()
    if not candidates:
        st.info("輸入目的地後，系統會推薦 3 個住宿候補。")
        st.session_state.pop(SESSION_SELECTED_HOTEL, None)
        return None

    labels = {
        candidate.place_id: f"{index + 1}. {candidate.name}"
        for index, candidate in enumerate(candidates)
        if candidate.place_id
    }
    current_selection = st.session_state.get(SESSION_SELECTED_HOTEL)
    current_place_id = current_selection.place_id if isinstance(current_selection, PlaceStop) else None
    if current_place_id not in labels:
        current_place_id = next(iter(labels))
    selected_place_id = st.radio(
        "住宿候補",
        options=list(labels.keys()),
        format_func=labels.get,
        index=list(labels.keys()).index(current_place_id),
    )
    selected_hotel = next(candidate for candidate in candidates if candidate.place_id == selected_place_id)
    st.session_state[SESSION_SELECTED_HOTEL] = selected_hotel
    return selected_hotel


def _select_effective_hotel(*, candidate_hotel: PlaceStop | None, manual_hotel: PlaceStop | None) -> PlaceStop | None:
    return manual_hotel or candidate_hotel


def _merge_preview_hotels(candidates: list[PlaceStop], manual_hotel: PlaceStop | None) -> list[PlaceStop]:
    if manual_hotel is None:
        return candidates
    if any(candidate.place_id == manual_hotel.place_id for candidate in candidates if candidate.place_id):
        return candidates
    return [*candidates, manual_hotel]


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
    st.session_state[SESSION_RESULT] = workflow.resume(decision)
    st.rerun()


def _require_streamlit() -> None:
    if st is None:
        raise ModuleNotFoundError("streamlit is required to render the UI")


if __name__ == "__main__":
    main()
