from branchpoint import BranchPoint
from branchpoint.core.schema import LLM_CALL, TOOL_OUTPUT


def test_depends_on_context(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved", "amount": 42}

    @bp.llm("interpret_payment_history")
    def interpret_payment_history(prompt):
        return {"summary": prompt.upper()}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = f"Payment status: {payment['status']}; amount: {payment['amount']}"
        with bp.depends_on(payment):
            interpret_payment_history(prompt)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret_payment_history")

    assert payment_output.event_id in llm_call.input_refs


def test_nested_depends_on_context_merges_refs(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved"}

    @bp.tool("customer_lookup")
    def customer_lookup():
        return {"tier": "gold"}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": prompt}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        customer = customer_lookup()
        with bp.depends_on(payment):
            with bp.depends_on(customer):
                interpret("plain transformed prompt")

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    customer_output = _event(events, TOOL_OUTPUT, "customer_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")

    assert payment_output.event_id in llm_call.input_refs
    assert customer_output.event_id in llm_call.input_refs


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)
