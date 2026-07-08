"""Build NetworkX trace dependency graphs from recorded events."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import networkx as nx

from .event_store import EventStore
from .graph_types import (
    CONTROL_FLOW,
    EDGE_WEIGHTS,
    EXPLICIT_INPUT_REF,
    EXPLICIT_OUTPUT_REF,
    HANDOFF_DEPENDENCY,
    LLM_RESPONSE_DEPENDENCY,
    MEMORY_DEPENDENCY,
    PARENT_CHILD,
    PARENT_CHILD_ALIAS,
    RETRIEVAL_DEPENDENCY,
    ROUTING_DEPENDENCY,
    SEQUENCE,
    STATE_DEPENDENCY,
    TOOL_RESULT_DEPENDENCY,
    VALIDATION_DEPENDENCY,
    GraphEdge,
    deterministic_edge_id,
)
from .schema import (
    HANDOFF,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    RETRIEVAL_RESULT,
    ROUTING_DECISION,
    TOOL_OUTPUT,
    VALIDATION_CHECK,
    TraceEvent,
)


class GraphBuilder:
    def __init__(self, store: EventStore):
        self.store = store

    def build(self, run_id: str):
        events = self.store.list_events(run_id)
        edges = self.infer_edges(run_id, events)
        self.persist_edges(edges)
        return self.to_networkx(events, edges)

    def infer_edges(self, run_id: str, events: list[TraceEvent]) -> list[GraphEdge]:
        event_ids = {event.event_id for event in events}
        by_id = {event.event_id: event for event in events}
        edges: dict[str, GraphEdge] = {}

        def add(source: str, target: str, edge_type: str, confidence: float, reason: str, metadata: dict[str, Any] | None = None) -> None:
            if source == target or source not in event_ids or target not in event_ids:
                return
            edge_id = deterministic_edge_id(run_id, source, target, edge_type, reason)
            edges[edge_id] = GraphEdge(
                edge_id=edge_id,
                run_id=run_id,
                source_event_id=source,
                target_event_id=target,
                edge_type=edge_type,
                weight=EDGE_WEIGHTS[edge_type],
                confidence=confidence,
                reason=reason,
                metadata=metadata or {},
            )

        ordered = sorted(events, key=lambda event: (event.timestamp_start, event.event_id))

        for event in ordered:
            if event.parent_id:
                add(event.parent_id, event.event_id, PARENT_CHILD, 1.0, "Event parent_id points to parent event")
                add(event.parent_id, event.event_id, PARENT_CHILD_ALIAS, 1.0, "Event parent_id points to parent event")

        for previous, current in zip(ordered, ordered[1:]):
            add(previous.event_id, current.event_id, CONTROL_FLOW, 0.60, "Events occurred consecutively in the same run")
            add(previous.event_id, current.event_id, SEQUENCE, 0.60, "Events occurred consecutively in the same run")

        for event in ordered:
            for ref in event.input_refs:
                source = by_id.get(ref)
                edge_type = _dependency_type_for_source(source)
                add(ref, event.event_id, edge_type, 0.95, "Event explicitly listed source event as input_ref")
                add(ref, event.event_id, EXPLICIT_INPUT_REF, 0.95, "Event explicitly listed source event as input_ref")
            for ref in event.output_refs:
                add(event.event_id, ref, STATE_DEPENDENCY, 0.80, "Event explicitly listed target event as output_ref")
                add(event.event_id, ref, EXPLICIT_OUTPUT_REF, 0.80, "Event explicitly listed target event as output_ref")

        last_memory_write_by_key: dict[str, TraceEvent] = {}
        for event in ordered:
            memory_key = event.metadata.get("memory_key")
            if event.type == MEMORY_WRITE and isinstance(memory_key, str):
                last_memory_write_by_key[memory_key] = event
            elif event.type == MEMORY_READ and isinstance(memory_key, str) and memory_key in last_memory_write_by_key:
                add(
                    last_memory_write_by_key[memory_key].event_id,
                    event.event_id,
                    MEMORY_DEPENDENCY,
                    0.95,
                    "Memory read used key written by earlier memory write",
                    {"memory_key": memory_key},
                )

        for previous, current in zip(ordered, ordered[1:]):
            if previous.type == ROUTING_DECISION:
                add(previous.event_id, current.event_id, ROUTING_DEPENDENCY, 0.80, "Routing decision immediately preceded selected branch event")
            if previous.type == HANDOFF:
                add(previous.event_id, current.event_id, HANDOFF_DEPENDENCY, 0.80, "Handoff immediately preceded selected event")

        return list(edges.values())

    def persist_edges(self, edges: list[GraphEdge]) -> None:
        for edge in edges:
            self.store.append_edge(edge)

    def to_networkx(self, events: list[TraceEvent], edges: list[GraphEdge]):
        graph = nx.MultiDiGraph()
        for event in events:
            graph.add_node(
                event.event_id,
                event=event,
                type=event.type,
                name=event.name,
                status=event.status,
                timestamp_start=event.timestamp_start,
            )
        for edge in edges:
            graph.add_edge(
                edge.source_event_id,
                edge.target_event_id,
                key=edge.edge_id,
                edge_type=edge.edge_type,
                weight=edge.weight,
                confidence=edge.confidence,
                reason=edge.reason,
                metadata=edge.metadata,
            )
        return graph


def _dependency_type_for_source(source: TraceEvent | None) -> str:
    if source is None:
        return STATE_DEPENDENCY
    if source.type == TOOL_OUTPUT:
        return TOOL_RESULT_DEPENDENCY
    if source.type == RETRIEVAL_RESULT:
        return RETRIEVAL_DEPENDENCY
    if source.type in {MEMORY_READ, MEMORY_WRITE}:
        return MEMORY_DEPENDENCY
    if source.type == LLM_OUTPUT:
        return LLM_RESPONSE_DEPENDENCY
    if source.type == VALIDATION_CHECK:
        return VALIDATION_DEPENDENCY
    return STATE_DEPENDENCY
