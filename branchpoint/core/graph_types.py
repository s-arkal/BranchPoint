"""Graph dataclasses and edge constants."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .errors import EventContractError
from .schema import SCHEMA_VERSION, utc_now_iso, validate_schema_version

GRAPH_BUILDER_VERSION = "graph_builder.v1"
GRAPH_RULE_VERSION = "graph_rules.v1"

CONTROL_FLOW = "controlflow"
PARENT_CHILD = "parentchild"
SEQUENCE = "sequence"
PARENT_CHILD_ALIAS = "parent_child"
EXPLICIT_INPUT_REF = "explicit_input_ref"
EXPLICIT_OUTPUT_REF = "explicit_output_ref"
CUSTOM_DEPENDENCY = "custom_dependency"
TOOL_RESULT_DEPENDENCY = "toolresultdependency"
LLM_RESPONSE_DEPENDENCY = "llmresponsedependency"
MEMORY_DEPENDENCY = "memorydependency"
RETRIEVAL_DEPENDENCY = "retrievaldependency"
ROUTING_DEPENDENCY = "routingdependency"
HANDOFF_DEPENDENCY = "handoffdependency"
VALIDATION_DEPENDENCY = "validationdependency"
STATE_DEPENDENCY = "state_dependency"
STATE_DEPENDENCY_ALIAS = "statedependency"
SEMANTIC_REFERENCE = "semanticreference"

EDGE_SOURCE_EXPLICIT_USER = "explicit_user"
EDGE_SOURCE_EXPLICIT_ADAPTER = "explicit_adapter"
EDGE_SOURCE_INPUT_REF = "input_ref"
EDGE_SOURCE_OUTPUT_REF = "output_ref"
EDGE_SOURCE_PARENT_CHILD = "parent_child"
EDGE_SOURCE_CONTROL_FLOW = "controlflow"
EDGE_SOURCE_MEMORY_KEY_MATCH = "memory_key_match"
EDGE_SOURCE_STATE_PATH_MATCH = "state_path_match"
EDGE_SOURCE_ROUTING_FOLLOW = "routing_follow"
EDGE_SOURCE_HANDOFF_FOLLOW = "handoff_follow"
EDGE_SOURCE_GRAPH_BUILDER_INFERRED = "graph_builder_inferred"

EDGE_SOURCE_KINDS = {
    EDGE_SOURCE_EXPLICIT_USER,
    EDGE_SOURCE_EXPLICIT_ADAPTER,
    EDGE_SOURCE_INPUT_REF,
    EDGE_SOURCE_OUTPUT_REF,
    EDGE_SOURCE_PARENT_CHILD,
    EDGE_SOURCE_CONTROL_FLOW,
    EDGE_SOURCE_MEMORY_KEY_MATCH,
    EDGE_SOURCE_STATE_PATH_MATCH,
    EDGE_SOURCE_ROUTING_FOLLOW,
    EDGE_SOURCE_HANDOFF_FOLLOW,
    EDGE_SOURCE_GRAPH_BUILDER_INFERRED,
}

CANONICAL_EDGE_TYPES = {
    MEMORY_DEPENDENCY,
    TOOL_RESULT_DEPENDENCY,
    RETRIEVAL_DEPENDENCY,
    ROUTING_DEPENDENCY,
    HANDOFF_DEPENDENCY,
    VALIDATION_DEPENDENCY,
    STATE_DEPENDENCY,
    SEMANTIC_REFERENCE,
    LLM_RESPONSE_DEPENDENCY,
    PARENT_CHILD,
    CONTROL_FLOW,
    SEQUENCE,
    EXPLICIT_INPUT_REF,
    EXPLICIT_OUTPUT_REF,
    CUSTOM_DEPENDENCY,
}

EDGE_TYPE_ALIASES = {
    PARENT_CHILD_ALIAS: PARENT_CHILD,
    STATE_DEPENDENCY_ALIAS: STATE_DEPENDENCY,
}

EDGE_TYPES = CANONICAL_EDGE_TYPES | set(EDGE_TYPE_ALIASES)

EDGE_WEIGHTS = {
    MEMORY_DEPENDENCY: 1.00,
    TOOL_RESULT_DEPENDENCY: 0.90,
    SEMANTIC_REFERENCE: 0.80,
    RETRIEVAL_DEPENDENCY: 0.80,
    ROUTING_DEPENDENCY: 0.75,
    HANDOFF_DEPENDENCY: 0.70,
    VALIDATION_DEPENDENCY: 0.65,
    LLM_RESPONSE_DEPENDENCY: 0.60,
    STATE_DEPENDENCY: 0.60,
    STATE_DEPENDENCY_ALIAS: 0.60,
    PARENT_CHILD: 0.50,
    CONTROL_FLOW: 0.40,
    SEQUENCE: 0.40,
    PARENT_CHILD_ALIAS: 0.50,
    EXPLICIT_INPUT_REF: 0.95,
    EXPLICIT_OUTPUT_REF: 0.80,
    CUSTOM_DEPENDENCY: 0.50,
}


def deterministic_edge_id(run_id: str, source: str, target: str, edge_type: str, reason: str | None) -> str:
    key = f"{run_id}:{source}:{target}:{edge_type}:{reason or ''}"
    return "edge_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def deterministic_explicit_edge_id(
    run_id: str,
    source: str,
    target: str,
    edge_type: str,
    source_kind: str,
    reason: str | None,
) -> str:
    normalized_type = normalize_edge_type(edge_type)
    key = f"explicit:{run_id}:{source}:{target}:{normalized_type}:{source_kind}:{reason or ''}"
    return "edge_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def normalize_edge_type(edge_type: str) -> str:
    if edge_type in CANONICAL_EDGE_TYPES:
        return edge_type
    if edge_type in EDGE_TYPE_ALIASES:
        return EDGE_TYPE_ALIASES[edge_type]
    validate_edge_type(edge_type)
    return edge_type


def validate_edge_type(edge_type: str) -> None:
    if edge_type not in EDGE_TYPES:
        allowed = ", ".join(sorted(CANONICAL_EDGE_TYPES))
        aliases = ", ".join(f"{alias}->{target}" for alias, target in sorted(EDGE_TYPE_ALIASES.items()))
        raise EventContractError(
            f"Invalid BranchPoint edge_type {edge_type!r}; expected one of: {allowed}. "
            f"Compatibility aliases: {aliases}"
        )


def validate_edge_weight(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EventContractError(f"BranchPoint edge {name} must be a number between 0.0 and 1.0")
    resolved = float(value)
    if resolved < 0.0 or resolved > 1.0:
        raise EventContractError(f"BranchPoint edge {name} must be between 0.0 and 1.0")
    return resolved


def validate_edge_source_kind(source_kind: str) -> None:
    if source_kind not in EDGE_SOURCE_KINDS:
        allowed = ", ".join(sorted(EDGE_SOURCE_KINDS))
        raise EventContractError(f"Invalid BranchPoint edge source_kind {source_kind!r}; expected one of: {allowed}")


@dataclass
class GraphEdge:
    edge_id: str
    run_id: str

    source_event_id: str
    target_event_id: str

    edge_type: str
    schema_version: str = SCHEMA_VERSION
    weight: float = 0.4
    confidence: float = 1.0

    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.schema_version = validate_schema_version(self.schema_version)


@dataclass
class TraceGraph:
    run_id: str
    events: list[Any] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


@dataclass
class GraphBuild:
    build_id: str
    run_id: str
    builder_version: str = GRAPH_BUILDER_VERSION
    rule_version: str = GRAPH_RULE_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    status: str = "success"
    metadata: dict[str, Any] = field(default_factory=dict)
