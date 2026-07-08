"""Example 6: reserved BranchPoint kwargs."""

from common import (
    assert_graph_edge,
    assert_input_ref,
    assert_no_reserved_kwargs,
    find_event,
    new_branchpoint,
    print_trace_summary,
)


bp = new_branchpoint("examples.dependency_tracking.reserved_kwargs")
seen_interpret_args = []
seen_tool_args = []


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
    seen_interpret_args.append(prompt)
    return {"text": f"Decision from prompt length {len(prompt)}"}


@bp.tool("some_tool")
def some_tool(message: str):
    seen_tool_args.append(message)
    return {"message": message.upper()}


def main() -> None:
    with bp.trace("reserved kwargs") as trace:
        payment = get_payment_history("C123")
        prompt = f"Payment data: {payment}"

        interpret_payment_history(
            prompt,
            bp_depends_on=[payment],
        )

        manual_event = bp.emit(
            type="custom",
            name="manual_source",
            output={"note": "manual dependency source"},
        )

        some_tool(
            "hello",
            bp_input_refs=[manual_event.event_id],
        )

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)

    payment_output = find_event(events, "tooloutput", "get_payment_history")
    llm_call = find_event(events, "llmcall", "interpret_payment_history")
    manual_source = find_event(events, "custom", "manual_source")
    some_tool_call = find_event(events, "toolcall", "some_tool")

    if len(seen_interpret_args) != 1 or seen_interpret_args[0] != prompt:
        raise AssertionError("interpret_payment_history did not receive exactly the prompt argument")
    if seen_tool_args != ["hello"]:
        raise AssertionError("some_tool did not receive exactly the user argument")

    assert_no_reserved_kwargs(llm_call, "bp_depends_on")
    assert_no_reserved_kwargs(some_tool_call, "bp_input_refs")
    assert_input_ref(llm_call, payment_output)
    assert_input_ref(some_tool_call, manual_source)
    assert_graph_edge(graph, payment_output, llm_call)
    assert_graph_edge(graph, manual_source, some_tool_call)

    print_trace_summary(bp, trace.run_id, graph)


if __name__ == "__main__":
    main()
