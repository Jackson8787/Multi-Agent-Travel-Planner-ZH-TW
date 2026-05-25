from inspect import signature
from pathlib import Path

from openai import LengthFinishReasonError, OpenAI
from pydantic import BaseModel, Field

from travel_planner.config import Settings
from travel_planner.domain.models import DayPlanState, PlaceStop


class ItineraryProposal(BaseModel):
    candidates: list[list[str]] = Field(min_length=1, max_length=3)


class FoodProposal(BaseModel):
    lunch_candidates: list[str] = Field(default_factory=list)
    dinner_candidates: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    score: int = Field(ge=1, le=5)
    summary: str
    warnings: list[str] = Field(default_factory=list)


def build_azure_llm(settings: Settings, response_format: type[BaseModel]):
    client = OpenAI(
        base_url=str(settings.azure_openai_endpoint),
        api_key=settings.azure_openai_api_key.get_secret_value(),
    )
    return AzureStructuredLlm(
        client=client,
        deployment=settings.azure_openai_deployment,
        response_format=response_format,
    )


class AzureStructuredLlm:
    def __init__(self, client: OpenAI, deployment: str, response_format: type[BaseModel]):
        self.client = client
        self.deployment = deployment
        self.response_format = response_format
        self._parse_parameters = set(signature(self.client.beta.chat.completions.parse).parameters)

    def call(self, prompt: str):
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
    return Path(__file__).with_name("skills").joinpath(name).read_text()


class LiveAgentSuite:
    def __init__(self, settings: Settings):
        self._itinerary_runner = AgentRunner(
            llm=build_azure_llm(settings, response_format=ItineraryProposal)
        )
        self._food_llm = build_azure_llm(settings, response_format=FoodProposal)
        self._review_llm = build_azure_llm(settings, response_format=ReviewResult)

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

    def propose_food(self, places: list[PlaceStop]) -> FoodProposal:
        skill = _read_skill("food_agent.md")
        prompt = f"{skill}\nVerified places: {[place.name for place in places]}"
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
