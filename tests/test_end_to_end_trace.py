import subprocess
import sys
from pathlib import Path

from branchpoint import BranchPoint
from branchpoint.cli.main import main
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.graph_types import MEMORY_DEPENDENCY, STATE_DEPENDENCY, TOOL_RESULT_DEPENDENCY
from branchpoint.core.schema import (
    FAILURE_LABEL,
    FINAL_OUTPUT,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    TOOL_CALL,
    TOOL_OUTPUT,
    USER_REQUEST,
)
from examples.refund_agent.run_demo import run_refund_workflow


def test_end_to_end_trace_to_persisted_dependency_graph_and_cli(tmp_path, capsys):
    db_path = tmp_path / ".branchpoint" / "branchpoint.sqlite"
    bp = BranchPoint(project="refund-demo", db_path=str(db_path))

    @bp.tool("payment_history")
    def payment_history(customer_id: str):
        return {"customer_id": customer_id, "refund_eligible": True}

    with bp.trace("refund-workflow") as trace:
        req = bp.emit(type=USER_REQUEST, name="initial_request", output={"query": "Can C123 get a refund?"})
        payment_history("C123")
        tool_output = [event for event in bp.store.list_events(trace.run_id) if event.name == "payment_history"][-1]
        llm_call = bp.emit(type=LLM_CALL, name="interpret_payment_history", input_refs=[tool_output.event_id])
        llm_output = bp.emit(
            type=LLM_OUTPUT,
            name="interpret_payment_history",
            parent_id=llm_call.event_id,
            input_refs=[llm_call.event_id],
            output="The customer is not eligible for a refund.",
        )
        bp.emit(
            type=FINAL_OUTPUT,
            name="final_answer",
            input_refs=[req.event_id, llm_output.event_id],
            output={"answer": "The customer is not eligible for a refund."},
        )
        bp.emit(
            type=FAILURE_LABEL,
            name="evaluator_result",
            output={"failed": True, "reason": "Customer was actually eligible."},
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)
    assert graph.number_of_nodes() == 7
    assert graph.number_of_edges() >= 7
    assert bp.store.list_edges(trace.run_id)

    assert main(["--db", str(db_path), "runs"]) == 0
    assert "refund-workflow" in capsys.readouterr().out
    assert main(["--db", str(db_path), "events", trace.run_id]) == 0
    assert "finaloutput final_answer" in capsys.readouterr().out
    assert main(["--db", str(db_path), "graph", trace.run_id]) == 0
    assert "Graph for" in capsys.readouterr().out


def test_refund_demo_dependency_shape(tmp_path):
    bp = BranchPoint(
        project="refund-agent-demo",
        db_path=str(tmp_path / "branchpoint.sqlite"),
        provenance_mode="hybrid",
    )

    run_id = run_refund_workflow(bp)
    events = bp.store.list_events(run_id)
    graph = bp.graph_builder().build(run_id)

    assert _event(events, USER_REQUEST, "initial_request")
    assert _event(events, TOOL_CALL, "get_payment_history")
    tool_output = _event(events, TOOL_OUTPUT, "get_payment_history")
    llm_call = _event(events, LLM_CALL, "interpret_payment_history")
    llm_output = _event(events, LLM_OUTPUT, "interpret_payment_history")
    memory_write = _event(events, MEMORY_WRITE, "write_refund_status")
    memory_read = _event(events, MEMORY_READ, "read_refund_status")
    final_output = _event(events, FINAL_OUTPUT, "final_answer")
    failure_label = _event(events, FAILURE_LABEL, "evaluator_result")

    llm_details = llm_call.metadata["provenance"]["input_refs_detail"]
    assert any(detail["event_id"] == tool_output.event_id for detail in llm_details)
    assert any(detail["path"] == ["refund_eligible"] for detail in llm_details)
    assert llm_output.event_id in memory_write.input_refs
    assert final_output.input_refs == [memory_read.event_id]
    assert failure_label.input_refs == [final_output.event_id]

    assert _has_edge(graph, tool_output.event_id, llm_call.event_id, TOOL_RESULT_DEPENDENCY)
    assert _has_edge(graph, llm_output.event_id, memory_write.event_id, STATE_DEPENDENCY)
    assert _has_edge(graph, memory_write.event_id, memory_read.event_id, MEMORY_DEPENDENCY)
    assert _has_edge(graph, memory_read.event_id, final_output.event_id, STATE_DEPENDENCY)
    assert _has_edge(graph, final_output.event_id, failure_label.event_id, STATE_DEPENDENCY)


def test_refund_demo_script_smoke(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "examples/refund_agent/run_demo.py",
            "--db-path",
            str(tmp_path / "demo.sqlite"),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Run ID: " in result.stdout
    assert "Events:" in result.stdout
    assert "Graph Edges:" in result.stdout
    assert "tooloutput get_payment_history -> llmcall interpret_payment_history" in result.stdout
    assert "memoryread read_refund_status -> finaloutput final_answer" in result.stdout


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)


def _has_edge(graph, source: str, target: str, edge_type: str) -> bool:
    edge_data = graph.get_edge_data(source, target) or {}
    return any(data["edge_type"] == edge_type for data in edge_data.values())
