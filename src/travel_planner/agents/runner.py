from pathlib import Path

from pydantic import BaseModel, Field

from travel_planner.config import Settings


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
    from crewai import LLM

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


class AgentRunner:
    def __init__(self, llm):
        self.llm = llm

    def propose_itinerary(
        self, destination: str, pace_name: str, visited: list[str], rejected: list[str]
    ) -> ItineraryProposal:
        skill = Path(__file__).with_name("skills").joinpath("itinerary_agent.md").read_text()
        prompt = (
            f"{skill}\n"
            f"Destination: {destination}\n"
            f"Pace: {pace_name}\n"
            f"Visited: {visited}\n"
            f"Rejected: {rejected}"
        )
        return self.llm.call(prompt)
