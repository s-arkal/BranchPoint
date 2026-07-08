"""Shared helpers for BranchPoint dependency-tracking examples."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from branchpoint import BranchPoint  # noqa: E402
from branchpoint.core.schema import TraceEvent  # noqa: E402


def new_branchpoint(project: str) -> BranchPoint:
    db_path = REPO_ROOT / ".branchpoint" / "examples" / "dependency_tracking.sqlite"
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return BranchPoint(project=project, db_path=str(db_path))


def find_event(events: list[TraceEvent], event_type: str, name: str | None = None) -> TraceEvent:
    for event in events:
        if event.type == event_type and (name is None or event.name == name):
            return event
    raise AssertionError(f"Missing event type={event_type!r} name={name!r}")


def find_events(events: list[TraceEvent], event_type: str, name: str | None = None) -> list[TraceEvent]:
    return [
        event
        for event in events
        if event.type == event_type and (name is None or event.name == name)
    ]


def assert_input_ref(target_event: TraceEvent, source_event: TraceEvent) -> None:
    if source_event.event_id not in target_event.input_refs:
        raise AssertionError(
            f"{alias(target_event)} missing input_ref for {alias(source_event)} "
            f"({source_event.event_id})"
        )


def assert_no_reserved_kwargs(event: TraceEvent, *reserved_names: str) -> None:
    kwargs = ((event.input or {}).get("kwargs") or {})
    leaked = [name for name in reserved_names if name in kwargs]
    if leaked:
        raise AssertionError(f"{alias(event)} leaked reserved kwargs into the wrapped function: {leaked}")


def assert_graph_edge(
    graph: Any,
    source_event: TraceEvent,
    target_event: TraceEvent,
    edge_type: str | None = None,
) -> None:
    matching = graph_edges(graph, source_event, target_event, edge_type)
    if not matching:
        suffix = f" of type {edge_type}" if edge_type else ""
        raise AssertionError(f"Missing graph edge{suffix}: {alias(source_event)} -> {alias(target_event)}")


def graph_edges(
    graph: Any,
    source_event: TraceEvent,
    target_event: TraceEvent,
    edge_type: str | None = None,
) -> list[dict[str, Any]]:
    edge_data = graph.get_edge_data(source_event.event_id, target_event.event_id) or {}
    edges = list(edge_data.values())
    if edge_type is None:
        return edges
    return [edge for edge in edges if edge.get("edge_type") == edge_type]


def print_trace_summary(bp: BranchPoint, run_id: str, graph: Any) -> None:
    events = bp.store.list_events(run_id)
    aliases = {event.event_id: alias(event, index) for index, event in enumerate(events, start=1)}

    print(f"run ID: {run_id}")
    print("events:")
    for event in events:
        input_refs = [aliases.get(ref, short_id(ref)) for ref in event.input_refs]
        print(f"  {aliases[event.event_id]}: {event.type} {event.name or ''}".rstrip())
        print(f"    input_refs: {input_refs}")
        details = provenance_details(event)
        if details:
            print(f"    ref_details: {details}")

    print("graph edges:")
    edges = sorted(
        graph.edges(data=True),
        key=lambda item: (aliases.get(item[0], item[0]), aliases.get(item[1], item[1]), item[2].get("edge_type", "")),
    )
    for source_id, target_id, data in edges:
        print(
            f"  {aliases.get(source_id, short_id(source_id))} -> "
            f"{aliases.get(target_id, short_id(target_id))} "
            f"[{data.get('edge_type')}]"
        )


def print_ref_details(label: str, details: list[dict[str, Any]]) -> None:
    print(f"{label} ref details:")
    for detail in details:
        print(f"  {detail}")


def provenance_details(event: TraceEvent) -> list[dict[str, Any]]:
    provenance = event.metadata.get("provenance")
    if not isinstance(provenance, dict):
        return []
    details = provenance.get("input_refs_detail")
    return details if isinstance(details, list) else []


def alias(event: TraceEvent, index: int | None = None) -> str:
    if index is None:
        return f"{event.type}:{event.name or short_id(event.event_id)}"
    name = f" {event.name}" if event.name else ""
    return f"N{index} ({event.type}{name})"


def short_id(event_id: str) -> str:
    return event_id.split("_")[-1][:8]
