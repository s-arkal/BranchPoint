from branchpoint import BranchPoint
from branchpoint.cli.main import main
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.schema import FAILURE_LABEL, FINAL_OUTPUT, LLM_CALL, LLM_OUTPUT, USER_REQUEST


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
