import json

from branchpoint import BranchPoint
from branchpoint.core.schema import LLM_CALL, TOOL_OUTPUT


def test_prompt_builder_refs_and_add_json(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved", "amount": 42}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": str(prompt)}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = bp.prompt().add("Payment: ").add_json(payment)
        prompt_refs_inside_trace = bp.refs(prompt)
        interpret(prompt)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")

    assert str(prompt) == 'Payment: {"amount": 42, "status": "approved"}'
    assert prompt_refs_inside_trace == [payment_output.event_id]
    assert payment_output.event_id in llm_call.input_refs


def test_prompt_add_with_explicit_ref(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved"}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": str(prompt)}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = bp.prompt().add("Status: approved", ref=payment)
        interpret(prompt)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")

    assert payment_output.event_id in llm_call.input_refs
    assert bp.refs(bp.prompt().add("literal only")) == []


def test_prompt_add_with_explicit_field_ref_keeps_path(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"refund_eligible": True}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": str(prompt)}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = bp.prompt().add("Eligible: true", ref=payment["refund_eligible"])
        interpret(prompt)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")
    detail = llm_call.metadata["provenance"]["input_refs_detail"][0]

    assert payment_output.event_id in llm_call.input_refs
    assert detail["path"] == ["refund_eligible"]


def test_bp_format_preserves_refs(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved"}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": str(prompt)}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = bp.format("Payment: {payment}", payment=payment)
        interpret(prompt)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")

    assert str(prompt) == "Payment: {'status': 'approved'}"
    assert payment_output.event_id in llm_call.input_refs


def test_prompt_serializes_as_string(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved", "amount": 42}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": str(prompt)}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = bp.prompt().add("Payment: ").add_json(payment)
        interpret(prompt)

    llm_call = _event(bp.store.list_events(trace.run_id), LLM_CALL, "interpret")
    serialized = json.dumps(llm_call.input, sort_keys=True)

    assert llm_call.input["args"] == ['Payment: {"amount": 42, "status": "approved"}']
    assert "ProvenanceTracker" not in serialized
    assert "parts" not in serialized
    assert "refs" not in serialized
    assert "tracker" not in serialized


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)
