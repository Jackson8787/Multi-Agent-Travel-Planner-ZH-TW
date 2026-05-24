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
