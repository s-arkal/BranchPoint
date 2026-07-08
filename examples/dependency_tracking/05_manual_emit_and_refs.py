"""Example 5: manual emit and bp.refs."""

from common import (
    assert_graph_edge,
    assert_input_ref,
    find_event,
    new_branchpoint,
    print_trace_summary,
)


bp = new_branchpoint("examples.dependency_tracking.manual_emit_and_refs")


@bp.tool("get_payment_history")
def get_payment_history(customer_id: str):
    return {
        "customer_id": customer_id,
        "refund_eligible": True,
        "days_since_purchase": 7,
        "plan": "premium",
    }


@bp.llm("interpret_payment_history")
def interpret_payment_history(payment_history):
    return {"text": "The customer is not eligible for a refund."}


def main() -> None:
    with bp.trace("manual emit and refs") as trace:
        payment = get_payment_history("C123")
        interpretation = interpret_payment_history(payment)

        final_event = bp.emit(
            type="finaloutput",
            name="final_answer",
            input_refs=bp.refs(interpretation),
            output={"answer": interpretation["text"]},
        )

        bp.emit(
            type="failurelabel",
            name="evaluator_result",
            input_refs=[final_event.event_id],
            output={
                "failed": True,
                "reason": "Customer was eligible, but agent denied refund.",
            },
        )

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)

    llm_output = find_event(events, "llmoutput", "interpret_payment_history")
    final_output = find_event(events, "finaloutput", "final_answer")
    failure_label = find_event(events, "failurelabel", "evaluator_result")

    assert_input_ref(final_output, llm_output)
    assert_input_ref(failure_label, final_output)
    assert_graph_edge(graph, llm_output, final_output)
    assert_graph_edge(graph, final_output, failure_label)

    print_trace_summary(bp, trace.run_id, graph)


if __name__ == "__main__":
    main()
