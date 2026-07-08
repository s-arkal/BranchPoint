"""Example 4: field-level proxy tracking."""

from common import (
    assert_graph_edge,
    assert_input_ref,
    find_event,
    new_branchpoint,
    print_ref_details,
    print_trace_summary,
)


bp = new_branchpoint("examples.dependency_tracking.field_level_proxy")


@bp.tool("get_payment_history")
def get_payment_history(customer_id: str):
    return {
        "customer_id": customer_id,
        "refund_eligible": True,
        "days_since_purchase": 7,
        "plan": "premium",
    }


@bp.llm("decide_from_fields")
def decide_from_fields(refund_eligible, days_since_purchase):
    if bool(refund_eligible) and int(days_since_purchase) <= 30:
        return {"text": "The customer is eligible for a refund."}
    return {"text": "The customer is not eligible for a refund."}


def main() -> None:
    with bp.trace("field-level proxy tracking") as trace:
        payment = get_payment_history("C123")
        eligible = payment["refund_eligible"]
        days = payment["days_since_purchase"]
        eligible_refs = bp.refs(eligible)
        days_refs = bp.refs(days)
        eligible_details = bp.ref_details(eligible)
        days_details = bp.ref_details(days)
        decide_from_fields(eligible, days)

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)

    payment_output = find_event(events, "tooloutput", "get_payment_history")
    llm_call = find_event(events, "llmcall", "decide_from_fields")

    if payment_output.event_id not in eligible_refs:
        raise AssertionError("payment['refund_eligible'] did not carry the payment tooloutput event ID")
    if payment_output.event_id not in days_refs:
        raise AssertionError("payment['days_since_purchase'] did not carry the payment tooloutput event ID")
    assert_input_ref(llm_call, payment_output)
    assert_graph_edge(graph, payment_output, llm_call)

    print_ref_details("eligible", eligible_details)
    print_ref_details("days", days_details)
    print_trace_summary(bp, trace.run_id, graph)


if __name__ == "__main__":
    main()
