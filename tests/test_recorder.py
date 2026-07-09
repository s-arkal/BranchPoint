import pytest

from branchpoint import BranchPoint
from branchpoint.core.context import get_current_run_id
from branchpoint.core.errors import EventContractError, NoActiveTraceError
from branchpoint.core.schema import CUSTOM, ERROR, SUCCESS, TOOL_CALL, TOOL_OUTPUT, USER_REQUEST
from branchpoint.storage.blob_store import MAX_INLINE_BYTES


def test_manual_event_recording_persists_trace(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("test-run") as trace:
        assert get_current_run_id() == trace.run_id
        event = bp.emit(type=USER_REQUEST, name="initial_user_request", output={"query": "hello"})

    run = bp.store.get_run(trace.run_id)
    events = bp.store.list_events(trace.run_id)

    assert run is not None
    assert run.name == "test-run"
    assert run.status == SUCCESS
    assert events == [event]
    assert get_current_run_id() is None


def test_emit_requires_active_trace(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with pytest.raises(NoActiveTraceError):
        bp.emit(type=USER_REQUEST, output={"query": "hello"})


def test_tool_decorator_preserves_return_and_records_call_output(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("add")
    def add(a, b):
        return a + b

    with bp.trace("math") as trace:
        assert add(2, 3) == 5

    events = bp.store.list_events(trace.run_id)
    call = next(event for event in events if event.type == TOOL_CALL)
    output = next(event for event in events if event.type == TOOL_OUTPUT)
    assert output.parent_id == call.event_id
    assert output.input_refs == [call.event_id]
    assert output.output == 5


def test_decorated_exception_records_error_and_reraises(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("explode")
    def explode():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        with bp.trace("broken") as trace:
            explode()

    run = bp.store.get_run(trace.run_id)
    events = bp.store.list_events(trace.run_id)
    output = next(event for event in events if event.type == TOOL_OUTPUT)

    assert run is not None
    assert run.status == ERROR
    assert output.status == ERROR
    assert output.output == {"error_type": "ValueError", "error": "bad"}
    assert output.metadata["error_type"] == "ValueError"
    assert output.metadata["error_message"] == "bad"
    assert output.metadata["exception_repr"] == "ValueError('bad')"


def test_strict_event_contract_rejects_invalid_event_type_status_and_uncontrolled_custom(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("contract"):
        with pytest.raises(EventContractError):
            bp.emit(type="madeup")
        with pytest.raises(EventContractError):
            bp.emit(type=USER_REQUEST, status="done")
        with pytest.raises(EventContractError):
            bp.emit(type=CUSTOM, name="loose_custom")


def test_controlled_custom_event_and_non_strict_compatibility_mode(tmp_path):
    strict_bp = BranchPoint(project="demo", db_path=str(tmp_path / "strict.sqlite"))
    with strict_bp.trace("custom") as strict_trace:
        strict_event = strict_bp.emit(
            type=CUSTOM,
            name="manual",
            metadata={"custom_type": "demo.manual"},
        )

    compat_bp = BranchPoint(project="demo", db_path=str(tmp_path / "compat.sqlite"), strict_event_types=False)
    with compat_bp.trace("compat") as compat_trace:
        raw_event = compat_bp.emit(type="legacy_event")
        loose_custom = compat_bp.emit(type=CUSTOM, name="loose_custom")

    assert strict_bp.store.list_events(strict_trace.run_id) == [strict_event]
    assert compat_bp.store.list_events(compat_trace.run_id) == [raw_event, loose_custom]


def test_emit_rejects_explicit_context_mismatch(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("context") as trace:
        with pytest.raises(EventContractError):
            bp.emit(type=USER_REQUEST, run_id="run_other")
        with pytest.raises(EventContractError):
            bp.emit(type=USER_REQUEST, project_id="other")
        event = bp.emit(type=USER_REQUEST)

    assert event.run_id == trace.run_id


def test_large_payloads_are_externalized_with_hash_and_ref(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / ".branchpoint" / "branchpoint.sqlite"))
    large_output = {"text": "x" * (MAX_INLINE_BYTES + 1)}

    with bp.trace("large") as trace:
        event = bp.emit(type=USER_REQUEST, output=large_output)

    [saved] = bp.store.list_events(trace.run_id)
    assert saved.output is None
    assert saved.output_payload_ref is not None
    assert saved.output_hash is not None
    assert bp.blob_store.get_json(saved.output_payload_ref) == large_output
    assert event.output_payload_ref == saved.output_payload_ref
