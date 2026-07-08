"""Event store protocol."""

from __future__ import annotations

from typing import Protocol

from .graph_types import GraphEdge
from .schema import TraceEvent, TraceRun


class EventStore(Protocol):
    def create_run(self, run: TraceRun) -> None:
        ...

    def finish_run(self, run_id: str, status: str, failure_label: str | None = None) -> None:
        ...

    def get_run(self, run_id: str) -> TraceRun | None:
        ...

    def list_runs(self) -> list[TraceRun]:
        ...

    def append_event(self, event: TraceEvent) -> None:
        ...

    def list_events(self, run_id: str) -> list[TraceEvent]:
        ...

    def append_edge(self, edge: GraphEdge) -> None:
        ...

    def list_edges(self, run_id: str) -> list[GraphEdge]:
        ...
