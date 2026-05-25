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
    TripSpec,
    UserChoice,
    UserDecision,
)
from travel_planner.domain.pace import PaceLevel, get_pace_profile
from travel_planner.integrations.api_registry import PROVIDERS
from travel_planner.integrations.exchange_rates import ExchangeRateClient
from travel_planner.integrations.google_places import GooglePlacesClient, GroundingNotFound
from travel_planner.integrations.google_routes import GoogleRoutesClient, RouteResult
from travel_planner.observability.tracing import LangfuseTracer, NoOpTracer
from travel_planner.ui.map_component import render_verified_route_map
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


def render_trip_spec_form() -> None:
    _require_streamlit()
    st.header("旅程需求")
    with st.form("trip_spec"):
        col1, col2 = st.columns(2)
        with col1:
            destination = st.text_input("目的地", value="Osaka")
            days = st.slider("旅遊天數", min_value=1, max_value=5, value=5)
            budget_amount = st.text_input("總預算", value="25000")
            budget_currency = st.selectbox("預算幣別", options=["TWD"], index=0)
            interests = st.text_input("興趣", value="anime, food")
        with col2:
            hotel_name = st.text_input("住宿名稱", value="Hotel Monterey Grasmere Osaka")
            hotel_price = st.text_input("住宿總價 (JPY)", value="38000")
            hotel_price_url = st.text_input("住宿官方或訂房來源 URL")
            must_visit_name = st.text_input("必去景點", value="Universal Studios Japan")
            must_visit_price = st.text_input("必去景點價格 (JPY)", value="8600")
            must_visit_price_url = st.text_input("必去景點官方價格 URL")

        pace_level = st.radio(
            "旅遊節奏",
            options=list(PaceLevel),
            format_func=lambda value: f"{PACE_LABELS[value]}: {PACE_DESCRIPTIONS[value]}",
            index=1,
        )

        submitted = st.form_submit_button("開始規劃")

    if not submitted:
        return

    try:
        settings = Settings()
        trip_spec = _build_trip_spec(
            settings=settings,
            destination=destination,
            days=days,
            budget_amount=budget_amount,
            budget_currency=budget_currency,
            interests=interests,
            pace_level=pace_level,
            hotel_name=hotel_name,
            hotel_price=hotel_price,
            hotel_price_url=hotel_price_url,
            must_visit_name=must_visit_name,
            must_visit_price=must_visit_price,
            must_visit_price_url=must_visit_price_url,
        )
    except (ValidationError, ValueError, InvalidOperation) as error:
        st.session_state[SESSION_ERROR] = str(error)
        return
    except Exception as error:  # pragma: no cover - UI fallback
        st.session_state[SESSION_ERROR] = f"初始化 live workflow 失敗：{error}"
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
    labels = [
        "行程 Agent",
        "Places 驗證",
        "Routes 驗證",
        "美食 Agent",
        "Budget Gate",
        "檢查 Agent",
    ]
    st.columns(len(labels))
    for column, label in zip(st.columns(len(labels)), labels, strict=True):
        column.metric(label, "完成" if result.day_state.status.value != "DRAFT" else "待執行")

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
        st.error("此日行程需要人工處理。請調整住宿、景點或稍後重試。")


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


def _build_trip_spec(
    *,
    settings: Settings,
    destination: str,
    days: int,
    budget_amount: str,
    budget_currency: str,
    interests: str,
    pace_level: PaceLevel,
    hotel_name: str,
    hotel_price: str,
    hotel_price_url: str,
    must_visit_name: str,
    must_visit_price: str,
    must_visit_price_url: str,
) -> TripSpec:
    destination = destination.strip()
    hotel_name = hotel_name.strip()
    must_visit_name = must_visit_name.strip()
    must_visit_price = must_visit_price.strip()
    _validate_trip_spec_inputs(
        destination=destination,
        hotel_name=hotel_name,
        must_visit_name=must_visit_name,
        must_visit_price=must_visit_price,
    )

    places_client = GooglePlacesClient(settings.google_maps_api_key.get_secret_value())
    rates_client = ExchangeRateClient(settings.exchange_rate_api_key.get_secret_value())
    hotel = _ground_required_place(places_client, hotel_name, field_label="住宿名稱")
    fx_snapshot = rates_client.snapshot(base_currency="JPY", target_currency=budget_currency)
    trip_prices = [
        PriceRecord(
            item_id="hotel-total",
            item_name=hotel_name,
            category="lodging",
            amount_original=Decimal(hotel_price),
            currency_original="JPY",
            status=PriceStatus.USER_CONFIRMED_OFFICIAL_SOURCE,
            source_provider="User Confirmed Official Source",
            source_url=hotel_price_url or None,
        )
    ]
    must_visit: list[PlaceStop] = []
    if must_visit_name:
        grounded = _ground_required_place(places_client, must_visit_name, field_label="必去景點")
        grounded.locked = True
        must_visit.append(grounded)
        if must_visit_price:
            trip_prices.append(
                PriceRecord(
                    item_id="must-visit-1",
                    item_name=must_visit_name,
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
        hotel=hotel,
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
    if days < 1:
        raise ValueError("旅遊天數必須大於 0")
    if not budget_amount.strip():
        raise ValueError("總預算不能空白")
    if not lodging_budget_amount.strip():
        raise ValueError("住宿預算不能空白")
    if not selected_hotel_place_id:
        raise ValueError("請先從住宿候補中選擇一間住宿。")


def _build_must_visit_preview_queries(raw_text: str) -> list[str]:
    normalized = raw_text.replace("\n", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


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
