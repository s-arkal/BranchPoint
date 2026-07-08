import networkx as nx

from branchpoint import BranchPoint
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.schema import (
    HANDOFF,
    LLM_CALL,
    MEMORY_READ,
    MEMORY_WRITE,
    ROUTING_DECISION,
    TOOL_OUTPUT,
    USER_REQUEST,
)
from branchpoint.core.graph_types import (
    CONTROL_FLOW,
    EXPLICIT_INPUT_REF,
    HANDOFF_DEPENDENCY,
    MEMORY_DEPENDENCY,
    PARENT_CHILD,
    PARENT_CHILD_ALIAS,
    ROUTING_DEPENDENCY,
    SEQUENCE,
    STATE_DEPENDENCY,
    TOOL_RESULT_DEPENDENCY,
)


def edge_types(graph):
    return {data["edge_type"] for _, _, data in graph.edges(data=True)}


def test_graph_builder_returns_networkx_graph_and_persists_deduped_edges(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("graph") as trace:
        root = bp.emit(type=USER_REQUEST, name="root")
        tool_output = bp.emit(type=TOOL_OUTPUT, name="tool", parent_id=root.event_id)
        bp.emit(type=LLM_CALL, name="planner", input_refs=[tool_output.event_id])
        bp.emit(type=MEMORY_WRITE, name="write", metadata={"memory_key": "refund_status"})
        bp.emit(type=MEMORY_READ, name="read", metadata={"memory_key": "refund_status"})
        route = bp.emit(type=ROUTING_DECISION, name="route")
        handoff = bp.emit(type=HANDOFF, name="handoff")
        bp.emit(type=LLM_CALL, name="selected", input_refs=[route.event_id, handoff.event_id])

    builder = GraphBuilder(bp.store)
    graph = builder.build(trace.run_id)
    first_edge_count = len(bp.store.list_edges(trace.run_id))
    builder.build(trace.run_id)
    second_edge_count = len(bp.store.list_edges(trace.run_id))

    assert isinstance(graph, nx.MultiDiGraph)
    assert graph.number_of_nodes() == len(bp.store.list_events(trace.run_id))
    assert first_edge_count == second_edge_count

    types = edge_types(graph)
    assert PARENT_CHILD in types
    assert PARENT_CHILD_ALIAS in types
    assert CONTROL_FLOW in types
    assert SEQUENCE in types
    assert EXPLICIT_INPUT_REF in types
    assert TOOL_RESULT_DEPENDENCY in types
    assert MEMORY_DEPENDENCY in types
    assert ROUTING_DEPENDENCY in types
    assert HANDOFF_DEPENDENCY in types
    assert STATE_DEPENDENCY in types
