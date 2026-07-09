from branchpoint import BranchPoint
from branchpoint.core.errors import EventContractError
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.graph_types import (
    CONTROL_FLOW,
    CUSTOM_DEPENDENCY,
    EDGE_SOURCE_EXPLICIT_ADAPTER,
    EDGE_SOURCE_EXPLICIT_USER,
    EDGE_SOURCE_INPUT_REF,
    EXPLICIT_INPUT_REF,
    PARENT_CHILD,
    SEMANTIC_REFERENCE,
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


def test_explicit_edge_api_persists_and_graph_build_includes_edge(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        source = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        target = bp.emit(type=FINAL_OUTPUT, name="answer", auto_refs=False)
        edge = bp.edge(
            source.event_id,
            target.event_id,
            SEMANTIC_REFERENCE,
            weight=0.8,
            confidence=0.75,
            reason="final answer reused tool evidence",
            metadata={"field": "eligibility"},
        )

    persisted = bp.store.list_edges(trace.run_id)
    graph = GraphBuilder(bp.store).build(trace.run_id)
    graph_edge = _single_edge(graph, source.event_id, target.event_id, SEMANTIC_REFERENCE)

    assert edge in persisted
    assert edge.edge_id in graph[source.event_id][target.event_id]
    assert graph_edge["weight"] == 0.8
    assert graph_edge["confidence"] == 0.75
    assert graph_edge["reason"] == "final answer reused tool evidence"
    assert graph_edge["metadata"]["source_kind"] == EDGE_SOURCE_EXPLICIT_USER
    assert graph_edge["metadata"]["explicit"] is True
    assert graph_edge["metadata"]["field"] == "eligibility"


def test_explicit_edge_api_rejects_invalid_endpoints_and_self_edges(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run"):
        source = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        other = bp.emit(type=FINAL_OUTPUT, name="answer", auto_refs=False)

        try:
            bp.edge(source.event_id, "evt_missing", SEMANTIC_REFERENCE)
        except EventContractError as exc:
            assert "missing" in str(exc)
        else:
            raise AssertionError("expected missing target to be rejected")

        try:
            bp.edge(source.event_id, source.event_id, SEMANTIC_REFERENCE)
        except EventContractError as exc:
            assert "itself" in str(exc)
        else:
            raise AssertionError("expected self-edge to be rejected")

        assert bp.edge(
            source.event_id,
            other.event_id,
            SEMANTIC_REFERENCE,
            allow_self_edge=False,
        ).source_event_id == source.event_id


def test_explicit_edge_api_rejects_invalid_weight_confidence_and_edge_type(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run"):
        source = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        target = bp.emit(type=FINAL_OUTPUT, name="answer", auto_refs=False)

        for kwargs in ({"weight": 1.1}, {"confidence": -0.01}):
            try:
                bp.edge(source.event_id, target.event_id, SEMANTIC_REFERENCE, **kwargs)
            except EventContractError as exc:
                assert "between 0.0 and 1.0" in str(exc)
            else:
                raise AssertionError("expected numeric validation to reject edge")

        try:
            bp.edge(source.event_id, target.event_id, "unknown_dependency")
        except EventContractError as exc:
            assert "Invalid BranchPoint edge_type" in str(exc)
            assert "custom_dependency" in str(exc)
        else:
            raise AssertionError("expected unknown edge type to be rejected")


def test_explicit_edge_api_accepts_custom_dependency_escape_hatch(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        source = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        target = bp.emit(type=FINAL_OUTPUT, name="answer", auto_refs=False)
        edge = bp.edge(
            source.event_id,
            target.event_id,
            CUSTOM_DEPENDENCY,
            reason="domain-specific semantic dependency",
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert edge.edge_type == CUSTOM_DEPENDENCY
    assert _has_edge(graph, source.event_id, target.event_id, CUSTOM_DEPENDENCY)


def test_explicit_edges_are_deterministic_idempotent_and_accept_compatibility_alias(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        source = bp.emit(type=LLM_OUTPUT, name="draft", auto_refs=False)
        target = bp.emit(type=MEMORY_WRITE, name="save", auto_refs=False)
        first = bp.edge(
            source.event_id,
            target.event_id,
            "statedependency",
            source_kind=EDGE_SOURCE_EXPLICIT_ADAPTER,
            reason="adapter declared state write dependency",
        )
        second = bp.edge(
            source.event_id,
            target.event_id,
            "statedependency",
            source_kind=EDGE_SOURCE_EXPLICIT_ADAPTER,
            reason="adapter declared state write dependency",
        )

    builder = GraphBuilder(bp.store)
    builder.build(trace.run_id)
    first_count = len(bp.store.list_edges(trace.run_id))
    builder.build(trace.run_id)
    second_count = len(bp.store.list_edges(trace.run_id))
    explicit_edges = [
        edge
        for edge in bp.store.list_edges(trace.run_id)
        if edge.source_event_id == source.event_id
        and edge.target_event_id == target.event_id
        and edge.reason == "adapter declared state write dependency"
    ]

    assert first.edge_id == second.edge_id
    assert first.edge_type == STATE_DEPENDENCY
    assert first.metadata["source_kind"] == EDGE_SOURCE_EXPLICIT_ADAPTER
    assert first.metadata["edge_type_alias"] == "statedependency"
    assert first_count == second_count
    assert len(explicit_edges) == 1


def test_inferred_edges_include_source_provenance_metadata(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        tool_output = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        llm_call = bp.emit(type=LLM_CALL, name="interpret", input_refs=[tool_output.event_id], auto_refs=False)

    graph = GraphBuilder(bp.store).build(trace.run_id)
    edge = _single_edge(graph, tool_output.event_id, llm_call.event_id, TOOL_RESULT_DEPENDENCY)

    assert edge["metadata"]["source_kind"] == EDGE_SOURCE_INPUT_REF
    assert edge["metadata"]["graph_builder_inferred"] is True


def _has_edge(graph, source: str, target: str, edge_type: str) -> bool:
    return bool(_edges(graph, source, target, edge_type))


def _single_edge(graph, source: str, target: str, edge_type: str) -> dict:
    edges = _edges(graph, source, target, edge_type)
    assert len(edges) == 1
    return edges[0]


def _edges(graph, source: str, target: str, edge_type: str) -> list[dict]:
    edge_data = graph.get_edge_data(source, target) or {}
    return [data for data in edge_data.values() if data["edge_type"] == edge_type]
