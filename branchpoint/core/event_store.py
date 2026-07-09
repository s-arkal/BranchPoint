"""Event store protocol."""

from __future__ import annotations

from typing import Protocol

from .graph_types import GraphEdge
from .schema import Snapshot, TraceEvent, TraceRun


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

    def update_event_metadata(self, event_id: str, metadata: dict) -> None:
        ...

    def append_edge(self, edge: GraphEdge) -> None:
        ...

    def list_edges(self, run_id: str) -> list[GraphEdge]:
        ...

    def append_snapshot(self, snapshot: Snapshot) -> None:
        ...

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        ...

    def list_snapshots(
        self,
        run_id: str,
        *,
        event_id: str | None = None,
        kind: str | None = None,
    ) -> list[Snapshot]:
        ...
