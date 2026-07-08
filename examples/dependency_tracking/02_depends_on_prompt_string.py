"""Example 2: bp.depends_on for transformed prompt strings."""

from common import (
    assert_graph_edge,
    assert_input_ref,
    find_event,
    new_branchpoint,
    print_trace_summary,
)


bp = new_branchpoint("examples.dependency_tracking.depends_on_prompt_string")


@bp.tool("get_payment_history")
def get_payment_history(customer_id: str):
    return {
        "customer_id": customer_id,
        "refund_eligible": True,
        "days_since_purchase": 7,
        "plan": "premium",
    }


@bp.llm("interpret_payment_history")
def interpret_payment_history(prompt: str):
    return {"text": f"Decision from prompt length {len(prompt)}"}


def main() -> None:
    with bp.trace("depends_on prompt string") as trace:
        payment = get_payment_history("C123")
        prompt = f"""
Decide refund eligibility from this payment history:
{payment}
"""
        with bp.depends_on(payment):
            interpret_payment_history(prompt)

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)

    payment_output = find_event(events, "tooloutput", "get_payment_history")
    llm_call = find_event(events, "llmcall", "interpret_payment_history")

    assert_input_ref(llm_call, payment_output)
    assert_graph_edge(graph, payment_output, llm_call)

    print_trace_summary(bp, trace.run_id, graph)


if __name__ == "__main__":
    main()
