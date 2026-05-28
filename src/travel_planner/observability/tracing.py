from contextlib import nullcontext
from typing import Protocol


class Tracer(Protocol):
    def event(self, name: str, payload: dict) -> None: ...

    def span(self, name: str): ...


class NoOpTracer:
    def event(self, name: str, payload: dict) -> None:
        return None

    def span(self, name: str):
        return nullcontext()


class RecordingTracer:
    def __init__(self):
        self.events: list[dict] = []

    def event(self, name: str, payload: dict) -> None:
        self.events.append({"name": name, "payload": payload})

    def span(self, name: str):
        return nullcontext()


class LangfuseTracer:
    def __init__(self):
        from langfuse import get_client

        self.client = get_client()

    def event(self, name: str, payload: dict) -> None:
        with self.client.start_as_current_observation(as_type="event", name=name) as event:
            event.update(input=payload)

    def span(self, name: str):
        return self.client.start_as_current_span(name=name)
