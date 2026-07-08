"""Graph dataclasses and edge constants."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

CONTROL_FLOW = "controlflow"
PARENT_CHILD = "parentchild"
SEQUENCE = "sequence"
PARENT_CHILD_ALIAS = "parent_child"
EXPLICIT_INPUT_REF = "explicit_input_ref"
EXPLICIT_OUTPUT_REF = "explicit_output_ref"
TOOL_RESULT_DEPENDENCY = "toolresultdependency"
LLM_RESPONSE_DEPENDENCY = "llmresponsedependency"
MEMORY_DEPENDENCY = "memorydependency"
RETRIEVAL_DEPENDENCY = "retrievaldependency"
ROUTING_DEPENDENCY = "routingdependency"
HANDOFF_DEPENDENCY = "handoffdependency"
VALIDATION_DEPENDENCY = "validationdependency"
STATE_DEPENDENCY = "statedependency"
SEMANTIC_REFERENCE = "semanticreference"

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
    PARENT_CHILD: 0.50,
    CONTROL_FLOW: 0.40,
    SEQUENCE: 0.40,
    PARENT_CHILD_ALIAS: 0.50,
    EXPLICIT_INPUT_REF: 0.95,
    EXPLICIT_OUTPUT_REF: 0.80,
}


def deterministic_edge_id(run_id: str, source: str, target: str, edge_type: str, reason: str | None) -> str:
    key = f"{run_id}:{source}:{target}:{edge_type}:{reason or ''}"
    return "edge_" + hashlib.sha256(key.encode()).hexdigest()[:32]


@dataclass
class GraphEdge:
    edge_id: str
    run_id: str

    source_event_id: str
    target_event_id: str

    edge_type: str
    weight: float = 0.4
    confidence: float = 1.0

    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceGraph:
    run_id: str
    events: list[Any] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
