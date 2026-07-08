import asyncio

import pytest

from branchpoint import BranchPoint
from branchpoint.core.schema import ERROR, LLM_CALL, LLM_OUTPUT, MEMORY_READ, MEMORY_WRITE, TOOL_CALL, TOOL_OUTPUT


def test_decorator_auto_dependency(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"x": 1}

    @bp.llm("summarize")
    def summarize(payload):
        return {"text": str(payload["x"])}

    with bp.trace("run") as trace:
        tool_value = lookup()
        tool_refs_inside_trace = bp.refs(tool_value)
        tool_details_inside_trace = bp.ref_details(tool_value)
        summarize(tool_value)

    events = bp.store.list_events(trace.run_id)
    tool_output = _event(events, TOOL_OUTPUT, "lookup")
    llm_call = _event(events, LLM_CALL, "summarize")
    llm_output = _event(events, LLM_OUTPUT, "summarize")

    assert tool_refs_inside_trace == [tool_output.event_id]
    assert tool_details_inside_trace[0]["event_id"] == tool_output.event_id
    assert tool_details_inside_trace[0]["source_event_type"] == TOOL_OUTPUT
    assert tool_output.event_id in llm_call.input_refs
    assert tool_output.event_id in llm_output.input_refs
    assert llm_output.input_refs[0] == llm_call.event_id
    assert llm_call.metadata["provenance"]["input_refs_detail"][0]["event_id"] == tool_output.event_id


def test_llmoutput_to_memorywrite_dependency(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))
    memory = {}

    @bp.llm("draft")
    def draft():
        return {"text": "hello"}

    @bp.memory_write("save", exclude_args=[0])
    def save(store, key, value):
        store[key] = value
        return {"stored": key}

    with bp.trace("run") as trace:
        result = draft()
        save(memory, "answer", result)

    events = bp.store.list_events(trace.run_id)
    llm_output = _event(events, LLM_OUTPUT, "draft")
    memory_write = _event(events, MEMORY_WRITE, "save")

    assert llm_output.event_id in memory_write.input_refs
    assert memory_write.metadata["memory_key"] == "answer"


def test_exclude_args(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("make_memory")
    def make_memory():
        return {}

    @bp.memory_write("save", exclude_args=[0])
    def save(store, key, value):
        store[key] = value
        return {"stored": key}

    with bp.trace("run") as trace:
        memory = make_memory()
        save(memory, "answer", {"text": "hello"})

    events = bp.store.list_events(trace.run_id)
    memory_source = _event(events, TOOL_OUTPUT, "make_memory")
    memory_write = _event(events, MEMORY_WRITE, "save")

    assert memory_source.event_id not in memory_write.input_refs


def test_memory_read_attaches_return_provenance(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))
    memory = {"answer": {"text": "hello"}}

    @bp.memory_read("load", exclude_args=[0])
    def load(store, key):
        return store[key]

    with bp.trace("run") as trace:
        value = load(memory, "answer")
        refs_inside_trace = bp.refs(value)

    memory_read = _event(bp.store.list_events(trace.run_id), MEMORY_READ, "load")

    assert memory_read.event_id in refs_inside_trace


def test_nested_decorated_calls_preserve_parent_context(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("inner")
    def inner():
        return {"value": 1}

    @bp.tool("outer")
    def outer():
        return inner()

    with bp.trace("run") as trace:
        outer()

    events = bp.store.list_events(trace.run_id)
    outer_call = _event(events, TOOL_CALL, "outer")
    inner_call = _event(events, TOOL_CALL, "inner")

    assert inner_call.parent_id == outer_call.event_id


def test_decorator_exception_records_error_output_and_reraises(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("make_payload")
    def make_payload():
        return {"value": 1}

    @bp.llm("explode")
    def explode(payload):
        raise ValueError(f"bad {payload['value']}")

    with pytest.raises(ValueError):
        with bp.trace("run") as trace:
            payload = make_payload()
            explode(payload)

    events = bp.store.list_events(trace.run_id)
    tool_output = _event(events, TOOL_OUTPUT, "make_payload")
    llm_call = _event(events, LLM_CALL, "explode")
    llm_output = _event(events, LLM_OUTPUT, "explode")

    assert llm_output.status == ERROR
    assert llm_output.input_refs == [llm_call.event_id, tool_output.event_id]
    assert llm_output.output == {"error_type": "ValueError", "error": "bad 1"}


def test_async_decorator_dependency(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("async_lookup")
    async def async_lookup():
        await asyncio.sleep(0)
        return {"x": 1}

    @bp.llm("summarize")
    def summarize(payload):
        return {"text": str(payload["x"])}

    with bp.trace("run") as trace:
        payload = asyncio.run(async_lookup())
        summarize(payload)

    events = bp.store.list_events(trace.run_id)
    tool_output = _event(events, TOOL_OUTPUT, "async_lookup")
    llm_call = _event(events, LLM_CALL, "summarize")

    assert tool_output.event_id in llm_call.input_refs


def test_decorator_api_options_exclude_kwargs_and_static_metadata(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("ignored_source")
    def ignored_source():
        return {"x": 1}

    @bp.llm("summarize", exclude_kwargs=["ignored"], metadata={"owner": "tests"})
    def summarize(prompt, *, ignored):
        return {"text": prompt}

    with bp.trace("run") as trace:
        ignored = ignored_source()
        summarize("hello", ignored=ignored)

    events = bp.store.list_events(trace.run_id)
    tool_output = _event(events, TOOL_OUTPUT, "ignored_source")
    llm_call = _event(events, LLM_CALL, "summarize")

    assert tool_output.event_id not in llm_call.input_refs
    assert llm_call.metadata["owner"] == "tests"
    assert llm_call.metadata["provenance"]["input_refs_detail"] == []


def test_reserved_bp_depends_on_kwarg(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"status": "approved"}

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": prompt}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        prompt = f"status={payment['status']}"
        interpret(prompt, bp_depends_on=[payment])

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")

    assert "bp_depends_on" not in llm_call.input["kwargs"]
    assert payment_output.event_id in llm_call.input_refs


def test_reserved_bp_input_refs_and_metadata_kwargs(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.llm("interpret")
    def interpret(prompt):
        return {"summary": prompt}

    with bp.trace("run") as trace:
        manual_source = bp.emit(type=TOOL_OUTPUT, name="manual_source", auto_refs=False)
        interpret(
            "plain prompt",
            bp_input_refs=[manual_source.event_id],
            bp_metadata={"owner": "tests", "provenance": {"hint": "kept"}},
        )

    llm_call = _event(bp.store.list_events(trace.run_id), LLM_CALL, "interpret")

    assert "bp_input_refs" not in llm_call.input["kwargs"]
    assert "bp_metadata" not in llm_call.input["kwargs"]
    assert manual_source.event_id in llm_call.input_refs
    assert llm_call.metadata["owner"] == "tests"
    assert llm_call.metadata["provenance"]["hint"] == "kept"
    assert llm_call.metadata["provenance"]["input_refs_detail"][0]["event_id"] == manual_source.event_id


def test_reserved_bp_no_track_kwarg(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("untracked_lookup")
    def untracked_lookup():
        return {"status": "approved"}

    @bp.llm("interpret")
    def interpret(payload):
        return {"summary": str(payload)}

    with bp.trace("run") as trace:
        payment = untracked_lookup(bp_no_track=True)
        refs_inside_trace = bp.refs(payment)
        interpret(payment)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "untracked_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")

    assert refs_inside_trace == []
    assert "bp_no_track" not in _event(events, TOOL_CALL, "untracked_lookup").input["kwargs"]
    assert payment_output.event_id not in llm_call.input_refs


def test_decorator_field_read_dependency(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("payment_lookup")
    def payment_lookup():
        return {"refund_eligible": True, "amount": 42}

    @bp.llm("interpret")
    def interpret(eligible):
        return {"eligible": bool(eligible)}

    with bp.trace("run") as trace:
        payment = payment_lookup()
        eligible = payment["refund_eligible"]
        interpret(eligible)

    events = bp.store.list_events(trace.run_id)
    payment_output = _event(events, TOOL_OUTPUT, "payment_lookup")
    llm_call = _event(events, LLM_CALL, "interpret")
    detail = llm_call.metadata["provenance"]["input_refs_detail"][0]

    assert payment_output.event_id in llm_call.input_refs
    assert detail["event_id"] == payment_output.event_id
    assert detail["path"] == ["refund_eligible"]


def test_full_decorator_api_options(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("tracked_source")
    def tracked_source():
        return {"x": 1}

    @bp.tool("untracked_source", track_output=False)
    def untracked_source():
        return {"x": 2}

    @bp.tool("sidecar_source", provenance_mode="sidecar")
    def sidecar_source():
        return {"x": 3}

    @bp.llm("summarize", exclude_kwargs=["ignored"], track_output=False, provenance_mode="off", metadata={"owner": "tests"})
    def summarize(payload, *, ignored):
        return {"text": str(payload["x"])}

    with bp.trace("run") as trace:
        tracked = tracked_source()
        untracked = untracked_source()
        sidecar = sidecar_source()
        summary = summarize(tracked, ignored=sidecar)
        refs_inside_trace = {
            "tracked": bp.refs(tracked),
            "untracked": bp.refs(untracked),
            "sidecar": bp.refs(sidecar),
            "summary": bp.refs(summary),
        }

    events = bp.store.list_events(trace.run_id)
    tracked_output = _event(events, TOOL_OUTPUT, "tracked_source")
    untracked_output = _event(events, TOOL_OUTPUT, "untracked_source")
    sidecar_output = _event(events, TOOL_OUTPUT, "sidecar_source")
    llm_call = _event(events, LLM_CALL, "summarize")

    assert refs_inside_trace["tracked"] == [tracked_output.event_id]
    assert refs_inside_trace["untracked"] == []
    assert refs_inside_trace["sidecar"] == [sidecar_output.event_id]
    assert refs_inside_trace["summary"] == []
    assert tracked_output.event_id in llm_call.input_refs
    assert untracked_output.event_id not in llm_call.input_refs
    assert sidecar_output.event_id not in llm_call.input_refs
    assert llm_call.metadata["owner"] == "tests"


def test_tracker_state_clears_at_trace_end(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"x": 1}

    with bp.trace("run"):
        value = lookup()
        assert bp.refs(value)

    assert bp.refs(value) == []


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)
