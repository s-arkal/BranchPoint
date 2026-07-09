import asyncio

import pytest

from branchpoint import BranchPoint
from branchpoint.core.schema import ERROR, LLM_CALL, LLM_OUTPUT, MEMORY_READ, MEMORY_WRITE, TOOL_CALL, TOOL_OUTPUT
from branchpoint.core.schema import (
    CANCELLED,
    HANDOFF,
    RETRY,
    ROUTING_DECISION,
    TIMEOUT,
    VALIDATION_CHECK,
)


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


def test_phase06_single_event_decorators_record_timing_and_dependencies(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.validation("check_payload")
    def check_payload(payload):
        return {"valid": payload["ok"]}

    @bp.route("select_route")
    def select_route(validation):
        return "refund" if validation["valid"] else "manual"

    @bp.handoff("handoff_to_refunds")
    def handoff_to_refunds(route, *, bp_metadata=None):
        return {"team": route}

    @bp.retry("retry_lookup")
    def retry_lookup(route):
        return {"route": route, "attempt": 2}

    with bp.trace("run") as trace:
        validation = check_payload({"ok": True})
        route = select_route(validation)
        handoff_to_refunds(route, bp_metadata={"owner": "tests"})
        retry_lookup(route)

    events = bp.store.list_events(trace.run_id)
    validation_event = _event(events, VALIDATION_CHECK, "check_payload")
    route_event = _event(events, ROUTING_DECISION, "select_route")
    handoff_event = _event(events, HANDOFF, "handoff_to_refunds")
    retry_event = _event(events, RETRY, "retry_lookup")

    assert route_event.input_refs == [validation_event.event_id]
    assert "bp_metadata" not in handoff_event.input["kwargs"]
    assert handoff_event.metadata["owner"] == "tests"
    assert validation_event.metadata["operation"] == "validation"
    assert route_event.metadata["operation"] == "route"
    assert handoff_event.metadata["operation"] == "handoff"
    assert retry_event.metadata["operation"] == "retry"
    for event in (validation_event, route_event, handoff_event, retry_event):
        assert isinstance(event.metadata["latency_ms"], float)
        assert event.metadata["timestamp_start"]
        assert event.metadata["timestamp_end"]
        assert event.timestamp_end == event.metadata["timestamp_end"]


def test_timeout_exception_records_timeout_status_and_reraises(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("timeout")
    def timeout():
        raise TimeoutError("deadline exceeded")

    with pytest.raises(TimeoutError):
        with bp.trace("run") as trace:
            timeout()

    output = _event(bp.store.list_events(trace.run_id), TOOL_OUTPUT, "timeout")

    assert output.status == TIMEOUT
    assert output.metadata["timeout"] is True
    assert output.metadata["error_type"] == "TimeoutError"
    assert output.metadata["latency_ms"] >= 0


def test_async_cancelled_error_records_cancelled_and_reraises(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("cancel")
    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        with bp.trace("run") as trace:
            asyncio.run(cancel())

    run = bp.store.get_run(trace.run_id)
    output = _event(bp.store.list_events(trace.run_id), TOOL_OUTPUT, "cancel")

    assert run.status == CANCELLED
    assert output.status == CANCELLED
    assert output.metadata["cancelled"] is True


def test_streaming_llm_records_one_call_and_final_output(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.llm("stream_answer")
    def stream_answer(*, stream=False):
        yield "hello"
        yield " world"

    with bp.trace("run") as trace:
        chunks = list(stream_answer(stream=True))

    events = bp.store.list_events(trace.run_id)
    call = _event(events, LLM_CALL, "stream_answer")
    output = _event(events, LLM_OUTPUT, "stream_answer")

    assert chunks == ["hello", " world"]
    assert [event.type for event in events] == [LLM_CALL, LLM_OUTPUT]
    assert output.parent_id == call.event_id
    assert output.output == ["hello", " world"]
    assert output.metadata["streaming"] is True
    assert output.metadata["stream_recording_strategy"] == "single_call_final_output"
    assert output.metadata["stream_chunks"] == 2


def test_async_concurrent_traces_keep_context_isolated(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    async def lookup(label):
        await asyncio.sleep(0)
        return {"label": label}

    async def run_one(label):
        with bp.trace(label) as trace:
            await lookup(label)
            return trace.run_id

    async def run_all():
        return await asyncio.gather(run_one("a"), run_one("b"))

    first_run_id, second_run_id = asyncio.run(run_all())

    assert first_run_id != second_run_id
    for run_id in (first_run_id, second_run_id):
        events = bp.store.list_events(run_id)
        assert {event.run_id for event in events} == {run_id}
        assert [event.type for event in events] == [TOOL_CALL, TOOL_OUTPUT]


def _event(events, event_type, name):
    return next(event for event in events if event.type == event_type and event.name == name)
