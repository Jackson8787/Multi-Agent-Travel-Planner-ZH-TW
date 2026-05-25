from openai import LengthFinishReasonError

from travel_planner.agents.runner import (
    AgentRunner,
    AzureStructuredLlm,
    FoodProposal,
    ItineraryProposal,
    LiveAgentSuite,
    ReviewResult,
)
from travel_planner.domain.models import DayPlanState, PlaceStop


class FakeLlm:
    def call(self, prompt: str):
        assert "RELAXED" in prompt
        return ItineraryProposal(candidates=[["Osaka Castle", "Dotonbori"]])


class RecordingLlm:
    def __init__(self, proposal):
        self.proposal = proposal
        self.prompt = None

    def call(self, prompt: str):
        self.prompt = prompt
        return self.proposal


class FakeFoodLlm:
    def call(self, prompt: str):
        assert "Verified places" in prompt
        return FoodProposal(lunch_candidates=["Ichiran Dotonbori"], dinner_candidates=[])


class FakeReviewLlm:
    def call(self, prompt: str):
        assert "Warnings" in prompt
        return ReviewResult(score=4, summary="Looks good", warnings=["PACE_REPLANNED"])


class FakeParseResponse:
    def __init__(self, parsed):
        class ParsedMessage:
            def __init__(self, parsed):
                self.parsed = parsed

        class Choice:
            def __init__(self, parsed):
                self.message = ParsedMessage(parsed)

        self.choices = [Choice(parsed)]


class FakeOpenAIClient:
    def __init__(self, parsed):
        self._parsed = parsed
        self.last_kwargs = None
        self.beta = self
        self.chat = self
        self.completions = self

    def parse(self, *, model, messages, response_format, max_completion_tokens, reasoning_effort=None):
        kwargs = {
            "model": model,
            "messages": messages,
            "response_format": response_format,
            "max_completion_tokens": max_completion_tokens,
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        self.last_kwargs = kwargs
        return FakeParseResponse(self._parsed)


class FakeLengthCompletion:
    usage = "completion_tokens=2400"


class FakeRetryOpenAIClient:
    def __init__(self, parsed):
        self._parsed = parsed
        self.calls = []
        self.beta = self
        self.chat = self
        self.completions = self

    def parse(self, *, model, messages, response_format, max_completion_tokens, reasoning_effort=None):
        kwargs = {
            "model": model,
            "messages": messages,
            "response_format": response_format,
            "max_completion_tokens": max_completion_tokens,
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise LengthFinishReasonError(completion=FakeLengthCompletion())
        return FakeParseResponse(self._parsed)


def test_itinerary_prompt_contains_trip_constraints():
    runner = AgentRunner(llm=FakeLlm())

    proposal = runner.propose_itinerary(
        destination="Osaka",
        pace_name="RELAXED",
        visited=[],
        rejected=[],
        must_visit=[],
        remaining_slots=2,
        route_mode="AUTO",
        walking_preference="NORMAL",
    )

    assert proposal.candidates == [["Osaka Castle", "Dotonbori"]]


def test_itinerary_prompt_includes_must_visit_and_remaining_slots():
    llm = RecordingLlm(ItineraryProposal(candidates=[["Dotonbori"]]))
    runner = AgentRunner(llm=llm)

    runner.propose_itinerary(
        destination="Yokohama",
        pace_name="RELAXED",
        visited=["Cup Noodles Museum Yokohama"],
        rejected=["REJECT_ROUTE:No route"],
        must_visit=["Cup Noodles Museum Yokohama"],
        remaining_slots=1,
        route_mode="AUTO",
        walking_preference="PREFER_WALKING",
    )

    assert "Must visit: ['Cup Noodles Museum Yokohama']" in llm.prompt
    assert "Remaining slots: 1" in llm.prompt
    assert "Route mode: AUTO" in llm.prompt
    assert "Walking preference: PREFER_WALKING" in llm.prompt


def test_live_agent_suite_builds_food_prompt():
    suite = LiveAgentSuite.__new__(LiveAgentSuite)
    suite._itinerary_runner = AgentRunner(llm=FakeLlm())
    suite._food_llm = FakeFoodLlm()
    suite._review_llm = FakeReviewLlm()

    proposal = suite.propose_food([PlaceStop(name="Dotonbori", place_id="dotonbori")])

    assert proposal.lunch_candidates == ["Ichiran Dotonbori"]


def test_live_agent_suite_builds_review_prompt():
    suite = LiveAgentSuite.__new__(LiveAgentSuite)
    suite._itinerary_runner = AgentRunner(llm=FakeLlm())
    suite._food_llm = FakeFoodLlm()
    suite._review_llm = FakeReviewLlm()

    result = suite.review(
        DayPlanState(
            day=1,
            places=[PlaceStop(name="Dotonbori", place_id="dotonbori")],
            warnings=["USER_APPROVED_BUDGET_INCREASE"],
        )
    )

    assert result.score == 4


def test_azure_structured_llm_uses_openai_parse_api():
    client = FakeOpenAIClient(ItineraryProposal(candidates=[["Osaka Castle", "Dotonbori"]]))
    llm = AzureStructuredLlm(
        client=client,
        deployment="gpt-5-mini",
        response_format=ItineraryProposal,
    )

    proposal = llm.call("Plan a trip")

    assert proposal.candidates == [["Osaka Castle", "Dotonbori"]]
    assert client.last_kwargs["model"] == "gpt-5-mini"
    assert client.last_kwargs["response_format"] is ItineraryProposal
    assert client.last_kwargs["messages"][0]["content"] == "Plan a trip"
    assert client.last_kwargs["reasoning_effort"] == "low"
    assert client.last_kwargs["max_completion_tokens"] == 2400


def test_azure_structured_llm_retries_after_length_finish_reason():
    client = FakeRetryOpenAIClient(ItineraryProposal(candidates=[["Osaka Castle", "Dotonbori"]]))
    llm = AzureStructuredLlm(
        client=client,
        deployment="gpt-5-mini",
        response_format=ItineraryProposal,
    )

    proposal = llm.call("Plan a trip")

    assert proposal.candidates == [["Osaka Castle", "Dotonbori"]]
    assert len(client.calls) == 2
    assert client.calls[0]["reasoning_effort"] == "low"
    assert client.calls[0]["max_completion_tokens"] == 2400
    assert client.calls[1]["reasoning_effort"] == "minimal"
    assert client.calls[1]["max_completion_tokens"] == 4000
    assert "smallest valid JSON" in client.calls[1]["messages"][0]["content"]
