"""Example 3: provenance-preserving prompt construction."""

from common import (
    assert_graph_edge,
    assert_input_ref,
    find_event,
    find_events,
    new_branchpoint,
    print_trace_summary,
)


bp = new_branchpoint("examples.dependency_tracking.prompt_builder")


@bp.tool("get_payment_history")
def get_payment_history(customer_id: str):
    return {
        "customer_id": customer_id,
        "refund_eligible": True,
        "days_since_purchase": 7,
        "plan": "premium",
    }


@bp.llm("interpret_payment_history")
def interpret_payment_history(prompt):
    return {"text": f"Decision from prompt: {str(prompt)[:42]}"}


def main() -> None:
    with bp.trace("prompt builder") as trace:
        payment = get_payment_history("C123")
        prompt = (
            bp.prompt()
            .add("Decide refund eligibility from this payment history:\n")
            .add_json(payment)
            .add("\nReturn a short decision.")
        )
        prompt_refs = bp.refs(prompt)
        print(f"prompt string:\n{prompt}\n")
        interpretation = interpret_payment_history(prompt)
        formatted_prompt = bp.format("Formatted payment summary: {payment}", payment=payment)
        formatted_prompt_refs = bp.refs(formatted_prompt)
        print(f"bp.format prompt:\n{formatted_prompt}\n")
        interpret_payment_history(formatted_prompt)

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)

    payment_output = find_event(events, "tooloutput", "get_payment_history")
    llm_calls = find_events(events, "llmcall", "interpret_payment_history")
    prompt_llm_call = llm_calls[0]
    formatted_llm_call = llm_calls[1]

    if payment_output.event_id not in prompt_refs:
        raise AssertionError("bp.refs(prompt) did not include the payment tooloutput event ID")
    if payment_output.event_id not in formatted_prompt_refs:
        raise AssertionError("bp.refs(bp.format(...)) did not include the payment tooloutput event ID")
    assert_input_ref(prompt_llm_call, payment_output)
    assert_input_ref(formatted_llm_call, payment_output)
    assert_graph_edge(graph, payment_output, prompt_llm_call)
    assert_graph_edge(graph, payment_output, formatted_llm_call)
    if not bp.unwrap(interpretation)["text"]:
        raise AssertionError("interpretation was unexpectedly empty")

    print_trace_summary(bp, trace.run_id, graph)


if __name__ == "__main__":
    main()
