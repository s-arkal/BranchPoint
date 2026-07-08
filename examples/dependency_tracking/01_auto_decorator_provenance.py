"""Example 1: automatic decorator provenance."""

from common import (
    assert_graph_edge,
    assert_input_ref,
    find_event,
    new_branchpoint,
    print_trace_summary,
)


bp = new_branchpoint("examples.dependency_tracking.auto_decorator_provenance")


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
    return {
        "text": "The customer is not eligible for a refund.",
        "saw_plan": payment_history["plan"],
    }


@bp.memory_write("write_refund_status", exclude_args=[0])
def write_memory(memory: dict, key: str, value):
    memory[key] = value
    return {"key": key, "value": value}


def main() -> None:
    memory = {}
    with bp.trace("auto decorator provenance") as trace:
        payment = get_payment_history("C123")
        interpretation = interpret_payment_history(payment)
        write_memory(memory, "refund_status", interpretation)

    events = bp.store.list_events(trace.run_id)
    graph = bp.graph_builder().build(trace.run_id)

    payment_output = find_event(events, "tooloutput", "get_payment_history")
    llm_call = find_event(events, "llmcall", "interpret_payment_history")
    llm_output = find_event(events, "llmoutput", "interpret_payment_history")
    memory_write = find_event(events, "memorywrite", "write_refund_status")

    assert_input_ref(llm_call, payment_output)
    assert_input_ref(memory_write, llm_output)
    assert_graph_edge(graph, payment_output, llm_call)
    assert_graph_edge(graph, llm_output, memory_write)

    print_trace_summary(bp, trace.run_id, graph)


if __name__ == "__main__":
    main()
