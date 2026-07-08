from branchpoint import BranchPoint
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.graph_types import (
    CONTROL_FLOW,
    EXPLICIT_INPUT_REF,
    PARENT_CHILD,
    STATE_DEPENDENCY,
    TOOL_RESULT_DEPENDENCY,
    deterministic_edge_id,
)
from branchpoint.core.schema import (
    FAILURE_LABEL,
    FINAL_OUTPUT,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_WRITE,
    TOOL_CALL,
    TOOL_OUTPUT,
)


INPUT_REF_REASON = "Event explicitly listed source event as input_ref"


def test_graph_edges_from_input_refs(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        tool_output = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        llm_call = bp.emit(type=LLM_CALL, name="interpret", input_refs=[tool_output.event_id], auto_refs=False)
        llm_output = bp.emit(type=LLM_OUTPUT, name="interpret", parent_id=llm_call.event_id, auto_refs=False)
        memory_write = bp.emit(type=MEMORY_WRITE, name="save", input_refs=[llm_output.event_id], auto_refs=False)

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert _has_edge(graph, tool_output.event_id, llm_call.event_id, TOOL_RESULT_DEPENDENCY)
    assert _has_edge(graph, llm_output.event_id, memory_write.event_id, STATE_DEPENDENCY)


def test_finaloutput_to_failurelabel_dependency_edge(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        final_output = bp.emit(type=FINAL_OUTPUT, name="answer", auto_refs=False)
        failure_label = bp.emit(
            type=FAILURE_LABEL,
            name="evaluator",
            input_refs=[final_output.event_id],
            auto_refs=False,
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert _has_edge(graph, final_output.event_id, failure_label.event_id, STATE_DEPENDENCY)


def test_memorywrite_to_finaloutput_dependency_edge(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        memory_write = bp.emit(type=MEMORY_WRITE, name="save", auto_refs=False)
        final_output = bp.emit(
            type=FINAL_OUTPUT,
            name="answer",
            input_refs=[memory_write.event_id],
            auto_refs=False,
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert _has_edge(graph, memory_write.event_id, final_output.event_id, STATE_DEPENDENCY)


def test_edge_metadata_from_provenance_detail(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        tool_output = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        llm_call = bp.emit(
            type=LLM_CALL,
            name="interpret",
            input_refs=[tool_output.event_id],
            metadata={
                "provenance": {
                    "input_refs_detail": [
                        {
                            "event_id": tool_output.event_id,
                            "path": ["refund_eligible"],
                            "reason": "field_read",
                            "confidence": 0.9,
                        }
                    ]
                }
            },
            auto_refs=False,
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)
    edge = _single_edge(graph, tool_output.event_id, llm_call.event_id, TOOL_RESULT_DEPENDENCY)

    assert edge["metadata"]["paths"] == [["refund_eligible"]]
    assert edge["metadata"]["reasons"] == ["field_read"]
    assert edge["metadata"]["confidences"] == [0.9]
    assert edge["metadata"]["input_refs_detail"][0]["event_id"] == tool_output.event_id


def test_deterministic_edge_ids_and_duplicate_prevention(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        tool_output = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        llm_call = bp.emit(
            type=LLM_CALL,
            name="interpret",
            input_refs=[tool_output.event_id, tool_output.event_id],
            auto_refs=False,
        )

    builder = GraphBuilder(bp.store)
    first_edges = builder.infer_edges(trace.run_id, bp.store.list_events(trace.run_id))
    second_edges = builder.infer_edges(trace.run_id, bp.store.list_events(trace.run_id))

    first_ids = sorted(edge.edge_id for edge in first_edges)
    second_ids = sorted(edge.edge_id for edge in second_edges)
    expected_id = deterministic_edge_id(
        trace.run_id,
        tool_output.event_id,
        llm_call.event_id,
        TOOL_RESULT_DEPENDENCY,
        INPUT_REF_REASON,
    )

    assert first_ids == second_ids
    assert expected_id in first_ids
    assert sum(
        1
        for edge in first_edges
        if edge.source_event_id == tool_output.event_id
        and edge.target_event_id == llm_call.event_id
        and edge.edge_type == TOOL_RESULT_DEPENDENCY
    ) == 1

    builder.build(trace.run_id)
    first_persisted_count = len(bp.store.list_edges(trace.run_id))
    builder.build(trace.run_id)
    assert len(bp.store.list_edges(trace.run_id)) == first_persisted_count


def test_parent_child_and_dependency_edges_are_separate(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        tool_output = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        tool_call = bp.emit(
            type=TOOL_CALL,
            name="consumer",
            parent_id=tool_output.event_id,
            input_refs=[tool_output.event_id],
            auto_refs=False,
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert _has_edge(graph, tool_output.event_id, tool_call.event_id, PARENT_CHILD)
    assert _has_edge(graph, tool_output.event_id, tool_call.event_id, TOOL_RESULT_DEPENDENCY)
    assert _has_edge(graph, tool_output.event_id, tool_call.event_id, EXPLICIT_INPUT_REF)
    assert _has_edge(graph, tool_output.event_id, tool_call.event_id, CONTROL_FLOW)


def _has_edge(graph, source: str, target: str, edge_type: str) -> bool:
    return bool(_edges(graph, source, target, edge_type))


def _single_edge(graph, source: str, target: str, edge_type: str) -> dict:
    edges = _edges(graph, source, target, edge_type)
    assert len(edges) == 1
    return edges[0]


def _edges(graph, source: str, target: str, edge_type: str) -> list[dict]:
    edge_data = graph.get_edge_data(source, target) or {}
    return [data for data in edge_data.values() if data["edge_type"] == edge_type]
