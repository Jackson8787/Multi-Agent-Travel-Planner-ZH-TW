from travel_planner.observability.tracing import NoOpTracer, RecordingTracer


def test_recording_tracer_records_gate_decision_evidence():
    tracer = RecordingTracer()
    tracer.event("pace_conflict", {"day": 2, "minutes": 142, "limit": 90})

    assert tracer.events[0]["name"] == "pace_conflict"


def test_noop_tracer_never_blocks_workflow():
    NoOpTracer().event("budget_conflict", {"day": 2})
