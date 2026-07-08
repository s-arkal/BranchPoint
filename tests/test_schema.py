from dataclasses import dataclass
from datetime import datetime, timezone

from branchpoint.core.ids import new_event_id, new_run_id, new_span_id
from branchpoint.core.schema import (
    ERROR,
    FINAL_OUTPUT,
    SUCCESS,
    TOOL_CALL,
    TraceEvent,
    TraceRun,
    USER_REQUEST,
)
from branchpoint.core.serialization import safe_serialize


def test_trace_event_and_run_match_phase_one_schema():
    event = TraceEvent(
        event_id=new_event_id(),
        run_id=new_run_id(),
        project_id="demo",
        type=USER_REQUEST,
        name="initial",
        output={"query": "hello"},
    )
    run = TraceRun(run_id=event.run_id, project_id="demo", name="workflow", started_at=event.timestamp_start)

    assert event.event_id.startswith("evt_")
    assert event.run_id.startswith("run_")
    assert event.type == USER_REQUEST
    assert event.status == SUCCESS
    assert event.timestamp_start
    assert event.input_refs == []
    assert run.status == "running"
    assert TOOL_CALL == "toolcall"
    assert FINAL_OUTPUT == "finaloutput"
    assert ERROR == "error"
    assert new_span_id().startswith("span_")


def test_safe_serialize_never_requires_json_native_objects():
    @dataclass
    class Demo:
        value: int

    payload = {
        "dataclass": Demo(3),
        "set": {1, 2},
        "datetime": datetime(2026, 7, 8, tzinfo=timezone.utc),
        "exception": ValueError("bad"),
        "bytes": b"abc",
        "object": object(),
    }

    serialized = safe_serialize(payload)
    assert serialized["dataclass"] == {"value": 3}
    assert sorted(serialized["set"]) == [1, 2]
    assert serialized["exception"]["error_type"] == "ValueError"
    assert serialized["bytes"] == {"type": "bytes", "length": 3}
    assert "repr" in serialized["object"]
