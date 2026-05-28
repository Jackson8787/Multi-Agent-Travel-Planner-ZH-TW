import time as _time
from collections.abc import Callable
from inspect import signature
from pathlib import Path

from openai import LengthFinishReasonError, OpenAI
from pydantic import BaseModel, Field

from travel_planner.config import Settings
from travel_planner.domain.models import DayPlanState, MacroCitySegment, PlaceStop, SlotType, TimeSlot


class ItineraryProposal(BaseModel):
    candidates: list[list[TimeSlot]] = Field(min_length=1, max_length=3)


class FoodProposal(BaseModel):
    lunch_candidates: list[str] = Field(default_factory=list)
    dinner_candidates: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    score: int = Field(ge=1, le=5)
    summary: str
    warnings: list[str] = Field(default_factory=list)


class MacroPlanProposal(BaseModel):
    """Structured LLM output for the Phase 1 city-level overview."""

    city_segments: list[MacroCitySegment]


class IntentParseResult(BaseModel):
    """Structured LLM output for Phase 0 conversational onboarding."""

    destination: str | None = None
    days: int | None = None
    budget_amount: str | None = None  # numeric TWD string, e.g. "25000"
    budget_currency: str = "TWD"
    interests: str = ""
    must_visit_names: str = ""
    must_visit_price: str = ""
    pace_label: str | None = None  # 「悠閒」「一般」「密集」etc.
    traveler_type: str | None = None  # 「獨旅」「情侶」「家庭」「未指定」
    user_constraints: str = ""  # negative preferences / re-plan feedback e.g. "不要餃子主題"
    needs_clarification: bool = False
    clarification_question: str = ""
    is_ready: bool = False


def build_azure_llm(
    settings: Settings,
    response_format: type[BaseModel],
    agent_name: str = "LLM",
):
    client = OpenAI(
        base_url=str(settings.azure_openai_endpoint),
        api_key=settings.azure_openai_api_key.get_secret_value(),
    )
    return AzureStructuredLlm(
        client=client,
        deployment=settings.azure_openai_deployment,
        response_format=response_format,
        agent_name=agent_name,
    )


class AzureStructuredLlm:
    # Increment when metrics_callback signature changes — app.py staleness guard checks this.
    _METRICS_CB_VERSION = 2

    def __init__(
        self,
        client: OpenAI,
        deployment: str,
        response_format: type[BaseModel],
        agent_name: str = "LLM",
        metrics_callback: Callable[[str, int, int, float], None] | None = None,
    ):
        self.client = client
        self.deployment = deployment
        self.response_format = response_format
        self.agent_name = agent_name
        # Mutable — LiveAgentSuite.set_metrics_callback() updates this at runtime.
        self.metrics_callback = metrics_callback
        self._parse_parameters = set(signature(self.client.beta.chat.completions.parse).parameters)

    def call(self, prompt: str):
        _t0 = _time.perf_counter()
        try:
            response = self._parse(
                prompt=prompt,
                reasoning_effort="low",
                max_completion_tokens=2400,
            )
        except LengthFinishReasonError:
            retry_prompt = (
                f"{prompt}\n\n"
                "Important: respond with the smallest valid JSON only. "
                "Do not include any explanation or extra text."
            )
            response = self._parse(
                prompt=retry_prompt,
                reasoning_effort="minimal",
                max_completion_tokens=4000,
            )
        _elapsed = _time.perf_counter() - _t0
        # Extract prompt / completion tokens separately, fault-tolerantly (test fakes skip .usage)
        _prompt_tokens = 0
        _completion_tokens = 0
        try:
            _usage = getattr(response, "usage", None)
            if _usage is not None:
                _prompt_tokens = int(getattr(_usage, "prompt_tokens", 0) or 0)
                _completion_tokens = int(getattr(_usage, "completion_tokens", 0) or 0)
        except Exception:
            pass
        if self.metrics_callback is not None:
            self.metrics_callback(self.agent_name, _prompt_tokens, _completion_tokens, _elapsed)
        return response.choices[0].message.parsed

    def _parse(self, *, prompt: str, reasoning_effort: str, max_completion_tokens: int):
        kwargs = {
            "model": self.deployment,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": self.response_format,
            "max_completion_tokens": max_completion_tokens,
        }
        if "reasoning_effort" in self._parse_parameters:
            kwargs["reasoning_effort"] = reasoning_effort
        elif "reasoning" in self._parse_parameters:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        return self.client.beta.chat.completions.parse(**kwargs)


class AgentRunner:
    def __init__(self, llm):
        self.llm = llm

    def propose_itinerary(
        self,
        destination: str,
        pace_name: str,
        visited: list[str],
        rejected: list[str],
        must_visit: list[str],
        remaining_slots: int,
        route_mode: str,
        walking_preference: str,
    ) -> ItineraryProposal:
        skill = _read_skill("itinerary_agent.md")
        prompt = (
            f"{skill}\n"
            f"Destination: {destination}\n"
            f"Pace: {pace_name}\n"
            f"Visited: {visited}\n"
            f"Rejected: {rejected}\n"
            f"Must visit: {must_visit}\n"
            f"Remaining slots: {remaining_slots}\n"
            f"Route mode: {route_mode}\n"
            f"Walking preference: {walking_preference}"
        )
        return self.llm.call(prompt)


def _read_skill(name: str) -> str:
    return Path(__file__).with_name("skills").joinpath(name).read_text(encoding="utf-8")


class LiveAgentSuite:
    def __init__(self, settings: Settings):
        self._itinerary_runner = AgentRunner(
            llm=build_azure_llm(settings, response_format=ItineraryProposal, agent_name="Itinerary Agent")
        )
        self._macro_llm = build_azure_llm(settings, response_format=MacroPlanProposal, agent_name="Macro Planner")
        self._food_llm = build_azure_llm(settings, response_format=FoodProposal, agent_name="Food Agent")
        self._review_llm = build_azure_llm(settings, response_format=ReviewResult, agent_name="Reviewer Agent")
        self._intent_llm = build_azure_llm(settings, response_format=IntentParseResult, agent_name="Intent Parser")

    def set_metrics_callback(
        self, callback: Callable[[str, int, int, float], None] | None
    ) -> None:
        """Distribute a (agent_name, in_tokens, out_tokens, elapsed_s) callback to all LLM instances.

        Called by TravelWorkflow.start_day() / resume() after a callback is registered so
        every subsequent LLM call fires metrics back to the UI layer.
        """
        for _llm in (
            self._itinerary_runner.llm,
            self._macro_llm,
            self._food_llm,
            self._review_llm,
            self._intent_llm,
        ):
            if isinstance(_llm, AzureStructuredLlm):
                _llm.metrics_callback = callback

    def propose_itinerary(
        self,
        destination: str,
        pace_name: str,
        visited: list[str],
        rejected: list[str],
        must_visit: list[str],
        remaining_slots: int,
        route_mode: str,
        walking_preference: str,
    ) -> ItineraryProposal:
        return self._itinerary_runner.propose_itinerary(
            destination,
            pace_name,
            visited,
            rejected,
            must_visit,
            remaining_slots,
            route_mode,
            walking_preference,
        )

    def propose_macro_plan(
        self,
        destination: str,
        total_days: int,
        interests: list[str],
        day_slots: list[dict],
        hotel_specified: bool,
        user_constraints: list[str] | None = None,
    ) -> MacroPlanProposal:
        skill = _read_skill("macro_plan_agent.md")
        # Build a human-readable city-day grouping for the LLM
        cities: dict[str, list[int]] = {}
        for slot in day_slots:
            city = slot["city"]
            if city not in cities:
                cities[city] = []
            cities[city].append(slot["day"])
        city_summary = "; ".join(
            (
                f"{city}: 第 {days[0]}-{days[-1]} 天（{len(days)} 天）"
                if len(days) > 1
                else f"{city}: 第 {days[0]} 天（1 天）"
            )
            for city, days in cities.items()
        )
        constraints_block = ""
        if user_constraints:
            constraints_block = (
                "\n\n"
                "## ⛔ 使用者修改要求（HARD CONSTRAINTS — 絕對禁止違反）\n\n"
                "以下是使用者明確拒絕的項目。**無論任何理由，均不得出現在輸出的任何欄位中**，\n"
                "包含 key_places、theme、food_picks、hotel_candidates 及 notes。\n"
                "違反任一條即視為輸出無效，必須整體重新生成。\n\n"
                + "\n".join(f"- ❌ {c}" for c in user_constraints)
                + "\n\n請在輸出前執行 Pre-Flight Constraint Check（見技能說明），確認每個欄位均不違反上述限制。"
            )
        prompt = (
            f"{skill}\n\n"
            f"目的地：{destination}\n"
            f"總天數：{total_days}\n"
            f"旅遊興趣：{', '.join(interests)}\n"
            f"城市分配：{city_summary}\n"
            f"住宿已由使用者指定：{'是' if hotel_specified else '否'}"
            f"{constraints_block}"
        )
        return self._macro_llm.call(prompt)

    def propose_food(
        self,
        places: list[PlaceStop],
        remaining_budget: object | None = None,
        traveler_type: str = "",
    ) -> FoodProposal:
        skill = _read_skill("food_agent.md")
        context_lines: list[str] = []
        if remaining_budget is not None:
            context_lines.append(f"Remaining budget: {remaining_budget} TWD")
        if traveler_type:
            context_lines.append(f"Traveler type: {traveler_type}")
        context_block = ("\n" + "\n".join(context_lines)) if context_lines else ""
        prompt = f"{skill}{context_block}\nVerified places: {[place.name for place in places]}"
        return self._food_llm.call(prompt)

    def review(self, day_state: DayPlanState) -> ReviewResult:
        skill = _read_skill("reviewer_agent.md")
        prompt = (
            f"{skill}\n"
            f"Day: {day_state.day}\n"
            f"Places: {[place.name for place in day_state.places]}\n"
            f"Meals: {[meal.name for meal in day_state.meals]}\n"
            f"Warnings: {day_state.warnings}\n"
            f"Route: {day_state.route.model_dump() if day_state.route else None}"
        )
        return self._review_llm.call(prompt)

    def parse_user_intent(
        self,
        chat_history: list[dict],
        current_params: dict,
    ) -> IntentParseResult:
        """Extract or update TripSpec parameters from the ongoing chat conversation."""
        import json as _json

        skill = _read_skill("intent_parser.md")
        # Render the last few turns (assistant + user) as readable text
        history_lines = []
        for msg in chat_history:
            role_label = "使用者" if msg["role"] == "user" else "AI助理"
            history_lines.append(f"{role_label}：{msg['content']}")
        history_text = "\n".join(history_lines)
        current_json = _json.dumps(current_params, ensure_ascii=False, indent=2)
        prompt = (
            f"{skill}\n\n"
            f"## 對話紀錄\n{history_text}\n\n"
            f"## 目前已擷取的參數\n```json\n{current_json}\n```"
        )
        return self._intent_llm.call(prompt)
