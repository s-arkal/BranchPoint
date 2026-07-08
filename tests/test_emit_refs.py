from branchpoint import BranchPoint
from branchpoint.core.schema import LLM_CALL, TOOL_OUTPUT


def test_manual_emit_auto_refs(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"status": "approved"}

    with bp.trace("run") as trace:
        payment = lookup()
        event = bp.emit(type=LLM_CALL, name="manual_llm", input={"payment": payment})

    tool_output = _event(bp.store.list_events(trace.run_id), TOOL_OUTPUT, "lookup")

    assert tool_output.event_id in event.input_refs
    assert event.metadata["provenance"]["input_refs_detail"][0]["event_id"] == tool_output.event_id


def test_manual_emit_merges_manual_and_auto_refs(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"status": "approved"}

    with bp.trace("run") as trace:
        payment = lookup()
        manual_source = bp.emit(type=TOOL_OUTPUT, name="manual_source", auto_refs=False)
        event = bp.emit(
            type=LLM_CALL,
            name="manual_llm",
            input=payment,
            input_refs=[manual_source.event_id, manual_source.event_id],
        )

    tool_output = _event(bp.store.list_events(trace.run_id), TOOL_OUTPUT, "lookup")

    assert event.input_refs == [tool_output.event_id, manual_source.event_id]


def test_emit_does_not_infer_refs_from_output(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"status": "approved"}

    with bp.trace("run") as trace:
        payment = lookup()
        event = bp.emit(type=LLM_CALL, name="manual_llm", output={"payment": payment})

    tool_output = _event(bp.store.list_events(trace.run_id), TOOL_OUTPUT, "lookup")

    assert tool_output.event_id not in event.input_refs
    assert event.input_refs == []


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)
