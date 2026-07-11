import asyncio
import json

import pytest

from branchpoint import BranchPoint
from branchpoint.cli.main import main
from branchpoint.core.graph_types import (
    HANDOFF_DEPENDENCY,
    MEMORY_DEPENDENCY,
    RETRIEVAL_DEPENDENCY,
    ROUTING_DEPENDENCY,
    SEMANTIC_REFERENCE,
    STATE_DEPENDENCY,
    TOOL_RESULT_DEPENDENCY,
    VALIDATION_DEPENDENCY,
)
from branchpoint.core.schema import (
    ERROR,
    FAILURE_LABEL,
    FINAL_OUTPUT,
    HANDOFF,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    RETRIEVAL_RESULT,
    ROUTING_DECISION,
    SNAPSHOT_LLM_PROMPT,
    SNAPSHOT_LLM_RESPONSE,
    SNAPSHOT_RETRIEVAL_RESULT,
    SNAPSHOT_STATE_AFTER,
    SNAPSHOT_STATE_BEFORE,
    SNAPSHOT_STATE_DIFF,
    SNAPSHOT_TOOL_OUTPUT,
    SUCCESS,
    TOOL_CALL,
    TOOL_OUTPUT,
    USER_REQUEST,
    VALIDATION_CHECK,
    VALIDATION_FAILED,
)
from branchpoint.core.serialization import REDACTED, hash_serialized_payload
from examples.refund_agent.run_demo import run_refund_workflow


def test_refund_agent_golden_trace_is_canonical_and_inspectable(tmp_path, capsys):
    db_path = tmp_path / "refund.sqlite"
    bp = BranchPoint(project="refund-agent-demo", db_path=str(db_path), provenance_mode="hybrid")

    run_id = run_refund_workflow(bp)
    events = bp.store.list_events(run_id)

    assert [(event.type, event.name, event.status) for event in events] == [
        (USER_REQUEST, "initial_request", SUCCESS),
        (TOOL_CALL, "get_payment_history", SUCCESS),
        (TOOL_OUTPUT, "get_payment_history", SUCCESS),
        (LLM_CALL, "interpret_payment_history", SUCCESS),
        (LLM_OUTPUT, "interpret_payment_history", SUCCESS),
        (MEMORY_WRITE, "write_refund_status", SUCCESS),
        (MEMORY_READ, "read_refund_status", SUCCESS),
        (FINAL_OUTPUT, "final_answer", SUCCESS),
        (FAILURE_LABEL, "evaluator_result", SUCCESS),
    ]

    tool_output = _event(events, TOOL_OUTPUT, "get_payment_history")
    llm_call = _event(events, LLM_CALL, "interpret_payment_history")
    llm_output = _event(events, LLM_OUTPUT, "interpret_payment_history")
    memory_write = _event(events, MEMORY_WRITE, "write_refund_status")
    memory_read = _event(events, MEMORY_READ, "read_refund_status")
    final_output = _event(events, FINAL_OUTPUT, "final_answer")
    failure_label = _event(events, FAILURE_LABEL, "evaluator_result")

    assert tool_output.output["refund_eligible"] is True
    assert tool_output.output_hash == hash_serialized_payload(tool_output.output)
    assert llm_output.output["refund_status"] == "not_eligible"
    assert llm_output.output_hash == hash_serialized_payload(llm_output.output)
    assert memory_write.output == {"key": "refund_status", "stored_status": "not_eligible"}
    assert memory_read.output == {"key": "refund_status", "stored_status": "not_eligible"}
    assert final_output.output == {"text": "The customer is not eligible for a refund."}
    assert failure_label.output["failed"] is True

    provenance_details = llm_call.metadata["provenance"]["input_refs_detail"]
    assert any(detail["event_id"] == tool_output.event_id for detail in provenance_details)
    assert any(detail["path"] == ["refund_eligible"] for detail in provenance_details)

    snapshots = bp.list_snapshots(run_id)
    assert [(snapshot.kind, snapshot.name) for snapshot in snapshots] == [
        (SNAPSHOT_TOOL_OUTPUT, "get_payment_history"),
        (SNAPSHOT_LLM_PROMPT, "interpret_payment_history"),
        (SNAPSHOT_LLM_RESPONSE, "interpret_payment_history"),
    ]
    for snapshot in snapshots:
        assert snapshot.payload_hash == hash_serialized_payload(bp.snapshot_payload(snapshot))

    graph = bp.graph_builder().build(run_id)
    expected_edges = {
        (tool_output.event_id, llm_call.event_id, TOOL_RESULT_DEPENDENCY),
        (llm_output.event_id, memory_write.event_id, STATE_DEPENDENCY),
        (memory_write.event_id, memory_read.event_id, MEMORY_DEPENDENCY),
        (memory_read.event_id, final_output.event_id, STATE_DEPENDENCY),
        (final_output.event_id, failure_label.event_id, STATE_DEPENDENCY),
    }
    assert expected_edges <= _edge_signatures(graph)
    assert bp.graph_builder().paths_to_failure(run_id, tool_output.event_id, failure_label.event_id, cutoff=8)
    assert bp.graph_builder().validate_graph(run_id)["status"] == "pass"
    assert bp.validate_run(run_id)["status"] == "pass"
    _assert_no_duplicate_edges_after_rebuild(bp, run_id)

    assert main(["--db", str(db_path), "events", run_id]) == 0
    assert "memoryread read_refund_status" in capsys.readouterr().out
    assert main(["--db", str(db_path), "graph", run_id, "--json"]) == 0
    graph_export = json.loads(capsys.readouterr().out)
    assert graph_export["schema_version"] == "branchpoint.graph_export.v1"
    assert any(edge["edge_type"] == MEMORY_DEPENDENCY for edge in graph_export["edges"])


def test_decorated_golden_scenarios_cover_success_errors_refs_and_async(tmp_path):
    bp = BranchPoint(project="golden", db_path=str(tmp_path / "golden.sqlite"), provenance_mode="hybrid")
    memory = {}

    @bp.tool("tool_success")
    def tool_success():
        return {"refund_eligible": True, "amount": 42}

    @bp.tool("tool_exception")
    def tool_exception():
        raise RuntimeError("tool exploded")

    @bp.retrieval("policy_search")
    def policy_search(query):
        return [{"doc_id": "refund-policy", "text": query}]

    @bp.llm("llm_success")
    def llm_success(payload):
        if isinstance(payload, list):
            return {"answer": f"docs={len(payload)}"}
        try:
            eligible = payload["refund_eligible"]
        except (KeyError, TypeError):
            eligible = payload
        return {"answer": f"eligible={eligible}"}

    @bp.llm("llm_parse_failure")
    def llm_parse_failure(_payload):
        raise ValueError("could not parse model JSON")

    @bp.memory_write("memory_save", exclude_args=[0])
    def memory_save(store, key, value):
        store[key] = value
        return {"stored": key}

    @bp.memory_read("memory_load", exclude_args=[0])
    def memory_load(store, key):
        return store[key]

    @bp.tool("inner_nested")
    def inner_nested():
        return {"inner": True}

    @bp.tool("outer_nested")
    def outer_nested():
        return inner_nested()

    @bp.tool("async_tool")
    async def async_tool():
        await asyncio.sleep(0)
        return {"async": True}

    with bp.trace("decorated-golden") as trace:
        tool_value = tool_success()
        field_value = tool_value["refund_eligible"]
        docs = policy_search("refund eligibility")
        llm_success(field_value)
        llm_success(docs)
        with pytest.raises(RuntimeError):
            tool_exception()
        with pytest.raises(ValueError):
            llm_parse_failure(tool_value)
        with bp.depends_on(tool_value, reason="golden_depends_on"):
            depends_event = bp.emit(type=FINAL_OUTPUT, name="depends_on_consumer", output={"ok": True})
        memory_save(memory, "answer", {"text": "approved"})
        memory_value = memory_load(memory, "answer")
        bp.emit(type=FINAL_OUTPUT, name="memory_consumer", input_refs=bp.refs(memory_value), output=bp.detach(memory_value))
        outer_nested()
        asyncio.run(async_tool())

    events = bp.store.list_events(trace.run_id)
    assert _event(events, TOOL_OUTPUT, "tool_success").status == SUCCESS
    assert _event(events, TOOL_OUTPUT, "tool_exception").status == ERROR
    assert _event(events, LLM_OUTPUT, "llm_success").status == SUCCESS
    assert _event(events, LLM_OUTPUT, "llm_parse_failure").status == ERROR
    assert _event(events, RETRIEVAL_RESULT, "policy_search").output == [
        {"doc_id": "refund-policy", "text": "refund eligibility"}
    ]
    assert _event(events, FINAL_OUTPUT, "depends_on_consumer").input_refs == [_event(events, TOOL_OUTPUT, "tool_success").event_id]

    field_call = _matching_event(events, LLM_CALL, "llm_success", lambda event: event.input["args"] == [True])
    field_detail = field_call.metadata["provenance"]["input_refs_detail"][0]
    assert field_detail["path"] == ["refund_eligible"]

    outer_call = _event(events, TOOL_CALL, "outer_nested")
    inner_call = _event(events, TOOL_CALL, "inner_nested")
    assert inner_call.parent_id == outer_call.event_id
    assert _event(events, TOOL_OUTPUT, "async_tool").output == {"async": True}

    graph = bp.graph_builder().build(trace.run_id)
    retrieval_result = _event(events, RETRIEVAL_RESULT, "policy_search")
    retrieval_call = _matching_event(
        events,
        LLM_CALL,
        "llm_success",
        lambda event: retrieval_result.event_id in event.input_refs,
    )
    assert _has_edge(graph, retrieval_result.event_id, retrieval_call.event_id, RETRIEVAL_DEPENDENCY)
    assert _has_edge(graph, _event(events, TOOL_OUTPUT, "tool_success").event_id, field_call.event_id, TOOL_RESULT_DEPENDENCY)
    assert _has_edge(graph, _event(events, TOOL_OUTPUT, "tool_success").event_id, depends_event.event_id, TOOL_RESULT_DEPENDENCY)
    assert _has_edge(graph, _event(events, MEMORY_WRITE, "memory_save").event_id, _event(events, MEMORY_READ, "memory_load").event_id, MEMORY_DEPENDENCY)
    assert _has_edge(graph, _event(events, MEMORY_READ, "memory_load").event_id, _event(events, FINAL_OUTPUT, "memory_consumer").event_id, STATE_DEPENDENCY)
    _assert_no_duplicate_edges_after_rebuild(bp, trace.run_id)

    snapshot_kinds = [snapshot.kind for snapshot in bp.list_snapshots(trace.run_id)]
    assert SNAPSHOT_TOOL_OUTPUT in snapshot_kinds
    assert SNAPSHOT_RETRIEVAL_RESULT in snapshot_kinds
    assert SNAPSHOT_LLM_PROMPT in snapshot_kinds
    assert SNAPSHOT_LLM_RESPONSE in snapshot_kinds


def test_golden_state_manual_edges_routing_handoff_validation_and_payload_safety(tmp_path):
    bp = BranchPoint(project="golden", db_path=str(tmp_path / ".branchpoint" / "golden.sqlite"), max_inline_bytes=64)

    @bp.route("choose_refund_path")
    def choose_refund_path():
        return {"route": "specialist"}

    @bp.tool("selected_tool")
    def selected_tool():
        return {"selected": True}

    @bp.handoff("handoff_to_specialist")
    def handoff_to_specialist():
        return {"agent": "refund-specialist"}

    @bp.llm("specialist_answer")
    def specialist_answer():
        return {"answer": "manual review"}

    with bp.trace("state-and-payload-golden") as trace:
        before = {"refund": {"eligible": False, "note": "keep"}}
        after = {"refund": {"eligible": True, "note": "keep"}}
        state_write = bp.state_write(["case", "refund"], before=before, after=after, state_name="case_state")
        state_read = bp.state_read("/case/refund/eligible", value=True, state_name="case_state")
        manual_source = bp.emit(type=USER_REQUEST, name="manual_source", output={"text": "manual evidence"})
        manual_target = bp.emit(type=FINAL_OUTPUT, name="manual_target", output={"text": "manual target"})
        manual_edge = bp.edge(
            manual_source.event_id,
            manual_target.event_id,
            SEMANTIC_REFERENCE,
            weight=0.8,
            confidence=0.9,
            reason="golden explicit edge",
            metadata={"assertion": "manual"},
        )
        choose_refund_path()
        selected_tool()
        handoff_to_specialist()
        specialist_answer()
        validation = bp.emit(
            type=VALIDATION_CHECK,
            name="final_answer_validation",
            input_refs=[manual_target.event_id],
            status=VALIDATION_FAILED,
            output={"valid": False, "reason": "contradicts eligibility evidence"},
        )
        failed_final = bp.emit(
            type=FINAL_OUTPUT,
            name="failed_final",
            input_refs=[validation.event_id],
            output={"answer": "not eligible"},
        )
        bp.emit(
            type=FAILURE_LABEL,
            name="failed_final_label",
            input_refs=[failed_final.event_id],
            output={"failed": True},
        )
        large_payload = bp.emit(type=USER_REQUEST, name="large_payload", output={"text": "x" * 200})
        redacted = bp.emit(
            type=USER_REQUEST,
            name="redacted_payload",
            output={"api_key": "sk-secret", "safe": "visible"},
        )

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)
    route_event = _event(events, ROUTING_DECISION, "choose_refund_path")
    selected_call = _event(events, TOOL_CALL, "selected_tool")
    handoff_event = _event(events, HANDOFF, "handoff_to_specialist")
    specialist_call = _event(events, LLM_CALL, "specialist_answer")

    assert state_write.metadata["state_path"] == "/case/refund"
    assert state_read.metadata["state_path"] == "/case/refund/eligible"
    assert _has_edge(graph, state_write.event_id, state_read.event_id, STATE_DEPENDENCY)
    assert _has_edge(graph, manual_source.event_id, manual_target.event_id, SEMANTIC_REFERENCE)
    assert _has_edge(graph, route_event.event_id, selected_call.event_id, ROUTING_DEPENDENCY)
    assert _has_edge(graph, handoff_event.event_id, specialist_call.event_id, HANDOFF_DEPENDENCY)
    assert _has_edge(graph, validation.event_id, failed_final.event_id, VALIDATION_DEPENDENCY)
    assert _has_edge(graph, failed_final.event_id, _event(events, FAILURE_LABEL, "failed_final_label").event_id, STATE_DEPENDENCY)
    assert manual_edge.metadata == {"assertion": "manual", "source_kind": "explicit_user", "explicit": True}

    state_snapshots = bp.list_snapshots(trace.run_id, event_id=state_write.event_id)
    assert [snapshot.kind for snapshot in state_snapshots] == [
        SNAPSHOT_STATE_BEFORE,
        SNAPSHOT_STATE_AFTER,
        SNAPSHOT_STATE_DIFF,
    ]
    assert bp.snapshot_payload(state_snapshots[2]) == [
        {"op": "replace", "path": "/refund/eligible", "value": True}
    ]
    assert state_snapshots[1].payload_hash == hash_serialized_payload(after)

    saved_large = _event(events, USER_REQUEST, "large_payload")
    assert large_payload.output_payload_ref is not None
    assert saved_large.output is None
    assert bp.event_payload(saved_large.event_id, "output") == {"text": "x" * 200}
    assert saved_large.output_hash == hash_serialized_payload({"text": "x" * 200})

    saved_redacted = _event(events, USER_REQUEST, "redacted_payload")
    assert redacted.output["api_key"] == REDACTED
    assert saved_redacted.output == {"api_key": REDACTED, "safe": "visible"}
    assert saved_redacted.metadata["payload_safety"]["output"]["redaction"]["paths"] == ["/api_key"]
    assert saved_redacted.output_hash == hash_serialized_payload({"api_key": REDACTED, "safe": "visible"})

    assert bp.graph_builder().validate_graph(trace.run_id)["status"] == "pass"
    assert bp.validate_run(trace.run_id)["status"] == "pass"
    _assert_no_duplicate_edges_after_rebuild(bp, trace.run_id)


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)


def _matching_event(events, event_type, name, predicate):
    return next(event for event in events if event.type == event_type and event.name == name and predicate(event))


def _has_edge(graph, source, target, edge_type):
    return (source, target, edge_type) in _edge_signatures(graph)


def _edge_signatures(graph):
    return {
        (source, target, data["edge_type"])
        for source, target, data in graph.edges(data=True)
    }


def _assert_no_duplicate_edges_after_rebuild(bp, run_id):
    builder = bp.graph_builder()
    builder.build(run_id)
    first_edges = bp.store.list_edges(run_id)
    first_count = len(first_edges)
    builder.build(run_id)
    second_edges = bp.store.list_edges(run_id)

    assert len(second_edges) == first_count
    assert len({edge.edge_id for edge in second_edges}) == len(second_edges)
    signatures = [
        (edge.source_event_id, edge.target_event_id, edge.edge_type, edge.reason)
        for edge in second_edges
    ]
    assert len(signatures) == len(set(signatures))
