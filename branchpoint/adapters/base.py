"""Stable framework adapter interfaces.

Adapters translate framework-native run and event objects into the BranchPoint
trace schema. They must stay thin: preserve application behavior, avoid
framework-specific mandatory dependencies, and use BranchPoint events, refs,
snapshots, and explicit edges instead of forking the graph model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from branchpoint.core.schema import TraceEvent, TraceRun

NativeContextT = TypeVar("NativeContextT")
NativeEventT = TypeVar("NativeEventT")

ADAPTER_DESIGN_RULES = (
    "Translate native framework activity into canonical BranchPoint TraceEvent objects.",
    "Preserve user application return values, exceptions, and scheduling behavior.",
    "Prefer explicit input_refs, output_refs, state paths, snapshots, and edges over opaque metadata.",
    "Store framework-specific fields under metadata without changing the BranchPoint schema.",
    "Do not implement scoring, ranking, replay, dashboards, graph databases, or cache acceleration.",
)

OPENTELEMETRY_MAPPING: Mapping[str, str] = {
    "trace_id": "TraceRun.metadata['otel.trace_id']",
    "span_id": "TraceEvent.span_id",
    "parent_span_id": "TraceEvent.parent_id when it maps to a BranchPoint event",
    "span_name": "TraceEvent.name",
    "span_status": "TraceEvent.status",
    "span_attributes": "TraceEvent.metadata['otel.attributes']",
    "span_events": "TraceEvent.metadata['otel.events']",
}

LANGGRAPH_ADAPTER_PREREQUISITES = (
    "Map graph node execution to canonical BranchPoint event types.",
    "Preserve native LangGraph state updates as state_read/state_write events or snapshots.",
    "Represent branch/control dependencies through input refs or explicit edges.",
    "Keep LangGraph imports optional and outside the core BranchPoint dependency set.",
)


@dataclass(frozen=True)
class AdapterRunContext:
    """Framework-neutral run context passed to future adapters."""

    project_id: str
    name: str | None = None
    run_id: str | None = None
    native_context: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FrameworkAdapter(Protocol[NativeContextT, NativeEventT]):
    """Protocol for thin framework adapters.

    Implementations may own a BranchPoint client internally, but the public
    contract is intentionally provider/framework agnostic. `to_trace_event`
    is the pure translation hook; `record_event` may persist the translated
    event and return it.
    """

    adapter_name: str

    def start_run(self, context: NativeContextT) -> TraceRun | None:
        """Start or attach to a BranchPoint run for a native framework context."""

    def record_event(self, native_event: NativeEventT) -> TraceEvent | None:
        """Record one native framework event as a BranchPoint event."""

    def end_run(self, context: NativeContextT) -> TraceRun | None:
        """Finish or detach from a BranchPoint run for a native framework context."""

    def to_trace_event(self, native_event: NativeEventT) -> TraceEvent:
        """Translate a native framework event into the BranchPoint schema."""


Adapter = FrameworkAdapter

__all__ = [
    "ADAPTER_DESIGN_RULES",
    "LANGGRAPH_ADAPTER_PREREQUISITES",
    "OPENTELEMETRY_MAPPING",
    "Adapter",
    "AdapterRunContext",
    "FrameworkAdapter",
]
