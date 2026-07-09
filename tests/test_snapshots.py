import json

import pytest

from branchpoint import BranchPoint, Snapshot
from branchpoint.core.errors import EventContractError
from branchpoint.core.schema import (
    LLM_CALL,
    LLM_OUTPUT,
    SNAPSHOT_CUSTOM,
    SNAPSHOT_LLM_PROMPT,
    SNAPSHOT_LLM_RESPONSE,
    SNAPSHOT_MEMORY_AFTER,
    SNAPSHOT_MEMORY_BEFORE,
    SNAPSHOT_RETRIEVAL_RESULT,
    SNAPSHOT_STATE_AFTER,
    SNAPSHOT_STATE_BEFORE,
    SNAPSHOT_STATE_DIFF,
    SNAPSHOT_TOOL_OUTPUT,
    STATE_WRITE,
    TOOL_OUTPUT,
)
from branchpoint.storage.blob_store import MAX_INLINE_BYTES


def test_manual_snapshot_persists_and_links_event_metadata(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        event = bp.emit(type=TOOL_OUTPUT, name="tool", output={"answer": 42})
        snapshot = bp.snapshot(
            kind=SNAPSHOT_CUSTOM,
            event_id=event.event_id,
            name="manual_evidence",
            payload={"answer": 42},
            metadata={"source": "test"},
        )

    saved_snapshot = bp.get_snapshot(snapshot.snapshot_id)
    [saved_event] = [saved_event for saved_event in bp.store.list_events(trace.run_id) if saved_event.event_id == event.event_id]

    assert isinstance(saved_snapshot, Snapshot)
    assert saved_snapshot.payload == {"answer": 42}
    assert saved_snapshot.payload_hash
    assert saved_snapshot.preview == {"answer": 42}
    assert saved_snapshot.metadata == {"source": "test"}
    assert saved_event.metadata["snapshot_ids"][-1] == snapshot.snapshot_id
    assert saved_event.metadata["snapshots"][SNAPSHOT_CUSTOM] == snapshot.snapshot_id
    assert bp.snapshot_payload(snapshot.snapshot_id) == {"answer": 42}


def test_large_snapshot_externalizes_blob_and_verifies_hash(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / ".branchpoint" / "branchpoint.sqlite"))
    payload = {"text": "x" * (MAX_INLINE_BYTES + 1)}

    with bp.trace("run") as trace:
        event = bp.emit(type=TOOL_OUTPUT, name="large", output={"ok": True})
        snapshot = bp.snapshot(kind=SNAPSHOT_CUSTOM, event_id=event.event_id, payload=payload)

    saved_snapshot = bp.store.get_snapshot(snapshot.snapshot_id)

    assert saved_snapshot.payload is None
    assert saved_snapshot.payload_ref is not None
    assert saved_snapshot.payload_hash
    assert saved_snapshot.preview["truncated"] is True
    assert bp.blob_store.get_json(saved_snapshot.payload_ref) == payload
    assert bp.snapshot_payload(saved_snapshot) == payload

    blob_path = tmp_path / ".branchpoint" / saved_snapshot.payload_ref
    blob_path.write_text(json.dumps({"text": "corrupted"}), encoding="utf-8")
    with pytest.raises(EventContractError):
        bp.snapshot_payload(saved_snapshot.snapshot_id)


def test_diff_represents_add_remove_and_replace(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    assert bp.diff(
        {"keep": 1, "remove": "gone", "nested": {"value": 1}},
        {"keep": 1, "add": "new", "nested": {"value": 2}},
    ) == [
        {"op": "remove", "path": "/remove"},
        {"op": "add", "path": "/add", "value": "new"},
        {"op": "replace", "path": "/nested/value", "value": 2},
    ]


def test_state_write_automatically_snapshots_before_after_and_diff(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write(
            "/refund",
            before={"eligible": False, "reason": "missing_receipt"},
            after={"eligible": True, "note": "manager_override"},
            state_name="refund_case",
        )

    snapshots = bp.list_snapshots(trace.run_id, event_id=write.event_id)
    snapshots_by_kind = {snapshot.kind: snapshot for snapshot in snapshots}
    saved_write = next(event for event in bp.store.list_events(trace.run_id) if event.type == STATE_WRITE)

    assert set(snapshots_by_kind) == {SNAPSHOT_STATE_BEFORE, SNAPSHOT_STATE_AFTER, SNAPSHOT_STATE_DIFF}
    assert snapshots_by_kind[SNAPSHOT_STATE_BEFORE].payload == {"eligible": False, "reason": "missing_receipt"}
    assert snapshots_by_kind[SNAPSHOT_STATE_AFTER].payload == {"eligible": True, "note": "manager_override"}
    assert snapshots_by_kind[SNAPSHOT_STATE_DIFF].payload == [
        {"op": "remove", "path": "/reason"},
        {"op": "add", "path": "/note", "value": "manager_override"},
        {"op": "replace", "path": "/eligible", "value": True},
    ]
    assert set(saved_write.metadata["snapshot_ids"]) == {snapshot.snapshot_id for snapshot in snapshots}
    assert saved_write.metadata["snapshots"][SNAPSHOT_STATE_DIFF] == snapshots_by_kind[SNAPSHOT_STATE_DIFF].snapshot_id


def test_decorators_automatically_snapshot_tool_retrieval_and_llm_payloads(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"tool": "result"}

    @bp.retrieval("search")
    def search(query):
        return [{"doc": query}]

    @bp.llm("draft")
    def draft(prompt):
        return {"text": f"answer {prompt}"}

    with bp.trace("run") as trace:
        lookup()
        search("policy")
        draft("hello")

    snapshots = bp.list_snapshots(trace.run_id)
    snapshots_by_kind = {snapshot.kind: snapshot for snapshot in snapshots}
    events = bp.store.list_events(trace.run_id)
    llm_call = next(event for event in events if event.type == LLM_CALL)
    llm_output = next(event for event in events if event.type == LLM_OUTPUT)
    tool_output = next(event for event in events if event.type == TOOL_OUTPUT and event.name == "lookup")

    assert snapshots_by_kind[SNAPSHOT_TOOL_OUTPUT].payload == {"tool": "result"}
    assert snapshots_by_kind[SNAPSHOT_RETRIEVAL_RESULT].payload == [{"doc": "policy"}]
    assert snapshots_by_kind[SNAPSHOT_LLM_PROMPT].payload["args"] == ["hello"]
    assert snapshots_by_kind[SNAPSHOT_LLM_RESPONSE].payload == {"text": "answer hello"}
    assert tool_output.metadata["snapshots"][SNAPSHOT_TOOL_OUTPUT] == snapshots_by_kind[SNAPSHOT_TOOL_OUTPUT].snapshot_id
    assert llm_call.metadata["snapshots"][SNAPSHOT_LLM_PROMPT] == snapshots_by_kind[SNAPSHOT_LLM_PROMPT].snapshot_id
    assert llm_output.metadata["snapshots"][SNAPSHOT_LLM_RESPONSE] == snapshots_by_kind[SNAPSHOT_LLM_RESPONSE].snapshot_id


def test_memory_snapshot_kinds_are_supported_explicitly(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        before = bp.snapshot(kind=SNAPSHOT_MEMORY_BEFORE, payload={"answer": None}, name="memory_before")
        after = bp.snapshot(kind=SNAPSHOT_MEMORY_AFTER, payload={"answer": "hello"}, name="memory_after")

    snapshots = bp.list_snapshots(trace.run_id)

    assert [snapshot.kind for snapshot in snapshots] == [SNAPSHOT_MEMORY_BEFORE, SNAPSHOT_MEMORY_AFTER]
    assert bp.snapshot_payload(before) == {"answer": None}
    assert bp.snapshot_payload(after.snapshot_id) == {"answer": "hello"}
