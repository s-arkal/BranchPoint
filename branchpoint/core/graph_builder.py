"""Build NetworkX trace dependency graphs from recorded events."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from typing import Any

import networkx as nx

from .event_store import EventStore
from .graph_types import (
    CONTROL_FLOW,
    EDGE_WEIGHTS,
    EDGE_SOURCE_CONTROL_FLOW,
    EDGE_SOURCE_GRAPH_BUILDER_INFERRED,
    EDGE_SOURCE_HANDOFF_FOLLOW,
    EDGE_SOURCE_INPUT_REF,
    EDGE_SOURCE_KINDS,
    EDGE_SOURCE_MEMORY_KEY_MATCH,
    EDGE_SOURCE_OUTPUT_REF,
    EDGE_SOURCE_PARENT_CHILD,
    EDGE_SOURCE_ROUTING_FOLLOW,
    EDGE_SOURCE_STATE_PATH_MATCH,
    EXPLICIT_INPUT_REF,
    EXPLICIT_OUTPUT_REF,
    GRAPH_BUILDER_VERSION,
    GRAPH_RULE_VERSION,
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
    GraphBuild,
    GraphEdge,
    deterministic_edge_id,
    validate_edge_type,
    validate_edge_weight,
)
from .errors import EventContractError
from .schema import (
    FINAL_OUTPUT,
    HANDOFF,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    METADATA_STATE_NAME,
    METADATA_STATE_PATH,
    RETRIEVAL_RESULT,
    ROUTING_DECISION,
    STATE_READ,
    STATE_WRITE,
    TOOL_CALL,
    TOOL_OUTPUT,
    VALIDATION_CHECK,
    TraceEvent,
    canonical_state_name,
    canonical_state_path,
    state_path_contains,
    utc_now_iso,
    validate_schema_version,
)


class GraphBuilder:
    def __init__(
        self,
        store: EventStore,
        *,
        builder_version: str = GRAPH_BUILDER_VERSION,
        rule_version: str = GRAPH_RULE_VERSION,
    ):
        self.store = store
        self.builder_version = builder_version
        self.rule_version = rule_version

    def build(self, run_id: str):
        events = self.store.list_events(run_id)
        inferred_edges = self.infer_edges(run_id, events)
        self.persist_edges(inferred_edges)
        edges = self.merge_persisted_edges(events, inferred_edges)
        self._record_build(
            run_id,
            status="success",
            metadata={
                "event_count": len(events),
                "inferred_edge_count": len(inferred_edges),
                "persisted_edge_count": len(self.store.list_edges(run_id)),
                "returned_edge_count": len(edges),
            },
        )
        return self.to_networkx(events, edges)

    def infer_edges(self, run_id: str, events: list[TraceEvent]) -> list[GraphEdge]:
        event_ids = {event.event_id for event in events}
        by_id = {event.event_id: event for event in events}
        edges: dict[str, GraphEdge] = {}

        def add(
            source: str,
            target: str,
            edge_type: str,
            confidence: float,
            reason: str,
            metadata: dict[str, Any] | None = None,
            source_kind: str = EDGE_SOURCE_GRAPH_BUILDER_INFERRED,
        ) -> None:
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
                metadata=_with_inferred_source_metadata(metadata, source_kind),
            )

        ordered = sorted(events, key=lambda event: (event.timestamp_start, event.event_id))

        for event in ordered:
            if event.parent_id:
                add(
                    event.parent_id,
                    event.event_id,
                    PARENT_CHILD,
                    1.0,
                    "Event parent_id points to parent event",
                    source_kind=EDGE_SOURCE_PARENT_CHILD,
                )
                add(
                    event.parent_id,
                    event.event_id,
                    PARENT_CHILD_ALIAS,
                    1.0,
                    "Event parent_id points to parent event",
                    source_kind=EDGE_SOURCE_PARENT_CHILD,
                )

        for previous, current in zip(ordered, ordered[1:]):
            add(
                previous.event_id,
                current.event_id,
                CONTROL_FLOW,
                0.60,
                "Events occurred consecutively in the same run",
                source_kind=EDGE_SOURCE_CONTROL_FLOW,
            )
            add(
                previous.event_id,
                current.event_id,
                SEQUENCE,
                0.60,
                "Events occurred consecutively in the same run",
                source_kind=EDGE_SOURCE_CONTROL_FLOW,
            )

        for event in ordered:
            for ref in event.input_refs:
                source = by_id.get(ref)
                edge_type = infer_dependency_edge_type(source, event)
                metadata = _provenance_edge_metadata(event, ref)
                add(
                    ref,
                    event.event_id,
                    edge_type,
                    0.95,
                    "Event explicitly listed source event as input_ref",
                    metadata,
                    EDGE_SOURCE_INPUT_REF,
                )
                add(
                    ref,
                    event.event_id,
                    EXPLICIT_INPUT_REF,
                    0.95,
                    "Event explicitly listed source event as input_ref",
                    metadata,
                    EDGE_SOURCE_INPUT_REF,
                )
            for ref in event.output_refs:
                add(
                    event.event_id,
                    ref,
                    STATE_DEPENDENCY,
                    0.80,
                    "Event explicitly listed target event as output_ref",
                    source_kind=EDGE_SOURCE_OUTPUT_REF,
                )
                add(
                    event.event_id,
                    ref,
                    EXPLICIT_OUTPUT_REF,
                    0.80,
                    "Event explicitly listed target event as output_ref",
                    source_kind=EDGE_SOURCE_OUTPUT_REF,
                )

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
                    EDGE_SOURCE_MEMORY_KEY_MATCH,
                )

        prior_state_writes: list[TraceEvent] = []
        for event in ordered:
            if event.type == STATE_WRITE and _state_ref(event) is not None:
                prior_state_writes.append(event)
                continue
            if event.type != STATE_READ:
                continue
            read_ref = _state_ref(event)
            if read_ref is None:
                continue
            matching_write = _latest_matching_state_write(prior_state_writes, read_ref)
            if matching_write is None:
                continue
            write_ref = _state_ref(matching_write)
            if write_ref is None:
                continue
            if _has_edge(edges.values(), matching_write.event_id, event.event_id, STATE_DEPENDENCY):
                continue
            match_kind = "exact" if write_ref["state_path"] == read_ref["state_path"] else "nested"
            add(
                matching_write.event_id,
                event.event_id,
                STATE_DEPENDENCY,
                0.95 if match_kind == "exact" else 0.85,
                "State read used path written by earlier state write",
                {
                    "state_name": read_ref["state_name"],
                    "state_path": read_ref["state_path"],
                    "read_state_path": read_ref["state_path"],
                    "write_state_path": write_ref["state_path"],
                    "path_match": match_kind,
                },
                EDGE_SOURCE_STATE_PATH_MATCH,
            )

        for previous, current in zip(ordered, ordered[1:]):
            if previous.type == ROUTING_DECISION:
                add(
                    previous.event_id,
                    current.event_id,
                    ROUTING_DEPENDENCY,
                    0.80,
                    "Routing decision immediately preceded selected branch event",
                    source_kind=EDGE_SOURCE_ROUTING_FOLLOW,
                )
            if previous.type == HANDOFF:
                add(
                    previous.event_id,
                    current.event_id,
                    HANDOFF_DEPENDENCY,
                    0.80,
                    "Handoff immediately preceded selected event",
                    source_kind=EDGE_SOURCE_HANDOFF_FOLLOW,
                )

        return list(edges.values())

    def persist_edges(self, edges: list[GraphEdge]) -> None:
        for edge in edges:
            self.store.append_edge(edge)

    def merge_persisted_edges(self, events: list[TraceEvent], inferred_edges: list[GraphEdge]) -> list[GraphEdge]:
        event_ids = {event.event_id for event in events}
        merged = {edge.edge_id: edge for edge in inferred_edges}
        if not events:
            return []
        run_id = events[0].run_id
        for edge in self.store.list_edges(run_id):
            if (
                edge.source_event_id in event_ids
                and edge.target_event_id in event_ids
                and edge.source_event_id != edge.target_event_id
            ):
                merged[edge.edge_id] = edge
        return _dedupe_state_dependency_edges([merged[edge_id] for edge_id in sorted(merged)])

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

    def export_json(self, run_id: str) -> dict[str, Any]:
        graph = self.build(run_id)
        builds = self.store.list_graph_builds(run_id)
        latest_build = builds[-1] if builds else None
        return {
            "schema_version": "branchpoint.graph_export.v1",
            "run_id": run_id,
            "builder_version": self.builder_version,
            "rule_version": self.rule_version,
            "build": _graph_build_to_dict(latest_build) if latest_build is not None else None,
            "nodes": [_event_to_node(graph.nodes[node]["event"]) for node in sorted(graph.nodes)],
            "edges": [
                _edge_to_dict(source, target, key, data)
                for source, target, key, data in sorted(
                    graph.edges(keys=True, data=True),
                    key=lambda item: (item[0], item[1], item[2]),
                )
            ],
        }

    def validate_graph(self, run_id: str) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        run = self.store.get_run(run_id)
        if run is None:
            errors.append({"code": "missing_run", "message": f"Run {run_id!r} does not exist"})
            events: list[TraceEvent] = []
        else:
            events = self.store.list_events(run_id)

        event_ids = {event.event_id for event in events}
        for event in events:
            if event.parent_id is not None and event.parent_id not in event_ids:
                errors.append(
                    {
                        "code": "broken_parent_id",
                        "event_id": event.event_id,
                        "parent_id": event.parent_id,
                        "message": "Event parent_id does not point to an event in the run",
                    }
                )
            _validate_event_refs(event, event_ids, "input_refs", errors)
            _validate_event_refs(event, event_ids, "output_refs", errors)

        if run is not None:
            self.build(run_id)
        edges = self.store.list_edges(run_id)
        for edge in edges:
            _validate_edge(edge, event_ids, errors, warnings)

        if run is not None and not events:
            warnings.append({"code": "empty_run", "message": "Run has no events"})

        return {
            "status": "fail" if errors else "pass",
            "run_id": run_id,
            "builder_version": self.builder_version,
            "rule_version": self.rule_version,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "event_count": len(events),
                "edge_count": len(edges),
                "error_count": len(errors),
                "warning_count": len(warnings),
            },
        }

    def downstream_dependents(self, run_id: str, event_id: str) -> list[str]:
        graph = self.build(run_id)
        _require_graph_node(graph, event_id)
        return _ordered_nodes(nx.descendants(graph, event_id), graph)

    def upstream_evidence(self, run_id: str, event_id: str) -> list[str]:
        graph = self.build(run_id)
        _require_graph_node(graph, event_id)
        return _ordered_nodes(nx.ancestors(graph, event_id), graph)

    def ancestors_of_failure(self, run_id: str, failure_event_id: str) -> list[str]:
        return self.upstream_evidence(run_id, failure_event_id)

    def paths_to_failure(
        self,
        run_id: str,
        source_event_id: str,
        failure_event_id: str,
        *,
        cutoff: int | None = None,
        max_paths: int = 100,
    ) -> list[list[str]]:
        graph = self.build(run_id)
        _require_graph_node(graph, source_event_id)
        _require_graph_node(graph, failure_event_id)
        paths: list[list[str]] = []
        for path in nx.all_simple_paths(graph, source_event_id, failure_event_id, cutoff=cutoff):
            paths.append(list(path))
            if len(paths) >= max_paths:
                break
        return paths

    def _record_build(self, run_id: str, *, status: str, metadata: dict[str, Any]) -> None:
        created_at = utc_now_iso()
        key = f"{run_id}:{self.builder_version}:{self.rule_version}:{created_at}:{status}"
        build = GraphBuild(
            build_id="gbuild_" + hashlib.sha256(key.encode()).hexdigest()[:24],
            run_id=run_id,
            builder_version=self.builder_version,
            rule_version=self.rule_version,
            created_at=created_at,
            status=status,
            metadata=metadata,
        )
        self.store.append_graph_build(build)


def infer_dependency_edge_type(source_event: TraceEvent | None, target_event: TraceEvent | None) -> str:
    if source_event is None or target_event is None:
        return STATE_DEPENDENCY
    source_type = source_event.type
    target_type = target_event.type

    if source_type == TOOL_OUTPUT and target_type in {LLM_CALL, TOOL_CALL, FINAL_OUTPUT}:
        return TOOL_RESULT_DEPENDENCY
    if source_type == RETRIEVAL_RESULT and target_type == LLM_CALL:
        return RETRIEVAL_DEPENDENCY
    if source_type == MEMORY_WRITE and target_type in {MEMORY_READ, LLM_CALL}:
        return MEMORY_DEPENDENCY
    if source_type == MEMORY_READ and target_type == LLM_CALL:
        return MEMORY_DEPENDENCY
    if source_type == STATE_WRITE and target_type == STATE_READ:
        return STATE_DEPENDENCY
    if source_type == LLM_OUTPUT and target_type == MEMORY_WRITE:
        return STATE_DEPENDENCY
    if source_type == LLM_OUTPUT and target_type == FINAL_OUTPUT:
        return LLM_RESPONSE_DEPENDENCY
    if source_type == VALIDATION_CHECK and target_type in {LLM_CALL, FINAL_OUTPUT}:
        return VALIDATION_DEPENDENCY
    if source_type == ROUTING_DECISION and target_type in {HANDOFF, TOOL_CALL, LLM_CALL}:
        return ROUTING_DEPENDENCY
    if source_type == HANDOFF and target_type in {LLM_CALL, TOOL_CALL}:
        return HANDOFF_DEPENDENCY
    return STATE_DEPENDENCY


def _provenance_edge_metadata(target_event: TraceEvent, source_event_id: str) -> dict[str, Any]:
    provenance = target_event.metadata.get("provenance")
    if not isinstance(provenance, dict):
        return {}
    details = provenance.get("input_refs_detail")
    if not isinstance(details, list):
        return {}

    matching_details = [
        detail
        for detail in details
        if isinstance(detail, dict) and detail.get("event_id") == source_event_id
    ]
    if not matching_details:
        return {}

    metadata: dict[str, Any] = {"input_refs_detail": matching_details}
    paths = _unique_metadata_values(detail.get("path", []) for detail in matching_details if "path" in detail)
    reasons = _unique_metadata_values(detail.get("reason") for detail in matching_details if detail.get("reason") is not None)
    confidences = _unique_metadata_values(
        detail.get("confidence") for detail in matching_details if detail.get("confidence") is not None
    )
    if paths:
        metadata["paths"] = paths
    if reasons:
        metadata["reasons"] = reasons
    if confidences:
        metadata["confidences"] = confidences
    return metadata


def _with_inferred_source_metadata(metadata: dict[str, Any] | None, source_kind: str) -> dict[str, Any]:
    edge_metadata = dict(metadata or {})
    edge_metadata["source_kind"] = source_kind
    edge_metadata["graph_builder_inferred"] = True
    return edge_metadata


def _state_ref(event: TraceEvent) -> dict[str, str] | None:
    try:
        state_name = canonical_state_name(event.metadata.get(METADATA_STATE_NAME))
        state_path = canonical_state_path(event.metadata.get(METADATA_STATE_PATH))
    except EventContractError:
        return None
    return {"state_name": state_name, "state_path": state_path}


def _latest_matching_state_write(
    prior_state_writes: list[TraceEvent],
    read_ref: dict[str, str],
) -> TraceEvent | None:
    for write_event in reversed(prior_state_writes):
        write_ref = _state_ref(write_event)
        if write_ref is None or write_ref["state_name"] != read_ref["state_name"]:
            continue
        if state_path_contains(write_ref["state_path"], read_ref["state_path"]):
            return write_event
    return None


def _has_edge(edges: Iterable[GraphEdge], source_event_id: str, target_event_id: str, edge_type: str) -> bool:
    return any(
        edge.source_event_id == source_event_id
        and edge.target_event_id == target_event_id
        and edge.edge_type == edge_type
        for edge in edges
    )


def _dedupe_state_dependency_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    state_path_match_pairs = {
        (edge.source_event_id, edge.target_event_id)
        for edge in edges
        if edge.edge_type == STATE_DEPENDENCY
        and edge.metadata.get("source_kind") != EDGE_SOURCE_STATE_PATH_MATCH
    }
    kept: dict[str, GraphEdge] = {}
    for edge in edges:
        if (
            edge.edge_type == STATE_DEPENDENCY
            and edge.metadata.get("source_kind") == EDGE_SOURCE_STATE_PATH_MATCH
            and (edge.source_event_id, edge.target_event_id) in state_path_match_pairs
        ):
            continue
        kept[edge.edge_id] = edge
    return [kept[edge_id] for edge_id in sorted(kept)]


def _unique_metadata_values(values: Iterable[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if marker not in seen:
            unique.append(value)
            seen.add(marker)
    return unique


def _graph_build_to_dict(build: GraphBuild) -> dict[str, Any]:
    return {
        "build_id": build.build_id,
        "run_id": build.run_id,
        "builder_version": build.builder_version,
        "rule_version": build.rule_version,
        "created_at": build.created_at,
        "status": build.status,
        "metadata": build.metadata,
    }


def _event_to_node(event: TraceEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "project_id": event.project_id,
        "schema_version": event.schema_version,
        "type": event.type,
        "name": event.name,
        "parent_id": event.parent_id,
        "span_id": event.span_id,
        "timestamp_start": event.timestamp_start,
        "timestamp_end": event.timestamp_end,
        "status": event.status,
        "input": event.input,
        "output": event.output,
        "input_refs": event.input_refs,
        "output_refs": event.output_refs,
        "metadata": event.metadata,
        "input_payload_ref": event.input_payload_ref,
        "output_payload_ref": event.output_payload_ref,
        "input_hash": event.input_hash,
        "output_hash": event.output_hash,
    }


def _edge_to_dict(source: str, target: str, key: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": key,
        "source_event_id": source,
        "target_event_id": target,
        "edge_type": data["edge_type"],
        "weight": data["weight"],
        "confidence": data["confidence"],
        "reason": data["reason"],
        "metadata": data["metadata"],
    }


def _validate_event_refs(
    event: TraceEvent,
    event_ids: set[str],
    field_name: str,
    errors: list[dict[str, Any]],
) -> None:
    refs = getattr(event, field_name)
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            errors.append(
                {
                    "code": "malformed_event_ref",
                    "event_id": event.event_id,
                    "field": field_name,
                    "ref": ref,
                    "message": f"Event {field_name} contains a malformed ref",
                }
            )
            continue
        if ref not in event_ids:
            errors.append(
                {
                    "code": "broken_event_ref",
                    "event_id": event.event_id,
                    "field": field_name,
                    "ref": ref,
                    "message": f"Event {field_name} points outside the run",
                }
            )


def _validate_edge(
    edge: GraphEdge,
    event_ids: set[str],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    try:
        validate_schema_version(edge.schema_version)
    except EventContractError as exc:
        errors.append({"code": "invalid_edge_schema_version", "edge_id": edge.edge_id, "message": str(exc)})
    try:
        validate_edge_type(edge.edge_type)
    except EventContractError as exc:
        errors.append({"code": "invalid_edge_type", "edge_id": edge.edge_id, "message": str(exc)})
    for name, value in (("weight", edge.weight), ("confidence", edge.confidence)):
        try:
            validate_edge_weight(name, value)
        except EventContractError as exc:
            errors.append({"code": f"invalid_edge_{name}", "edge_id": edge.edge_id, "message": str(exc)})
    if edge.source_event_id not in event_ids:
        errors.append(
            {
                "code": "invalid_edge_source",
                "edge_id": edge.edge_id,
                "event_id": edge.source_event_id,
                "message": "Edge source_event_id does not point to an event in the run",
            }
        )
    if edge.target_event_id not in event_ids:
        errors.append(
            {
                "code": "invalid_edge_target",
                "edge_id": edge.edge_id,
                "event_id": edge.target_event_id,
                "message": "Edge target_event_id does not point to an event in the run",
            }
        )
    if edge.source_event_id == edge.target_event_id:
        warnings.append(
            {
                "code": "self_edge",
                "edge_id": edge.edge_id,
                "message": "Edge points an event to itself",
            }
        )
    source_kind = edge.metadata.get("source_kind")
    if source_kind not in EDGE_SOURCE_KINDS:
        errors.append(
            {
                "code": "missing_edge_provenance",
                "edge_id": edge.edge_id,
                "source_kind": source_kind,
                "message": "Edge metadata must include a valid source_kind",
            }
        )


def _require_graph_node(graph: nx.MultiDiGraph, event_id: str) -> None:
    if event_id not in graph:
        raise EventContractError(f"BranchPoint graph does not contain event {event_id!r}")


def _ordered_nodes(nodes: Iterable[str], graph: nx.MultiDiGraph) -> list[str]:
    return sorted(nodes, key=lambda node: (graph.nodes[node].get("timestamp_start") or "", node))
