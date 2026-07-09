import json
import re
import sqlite3
from datetime import datetime, timezone

import pytest

from branchpoint import BranchPoint
from branchpoint.cli.main import main
from branchpoint.core.errors import EventContractError
from branchpoint.core.schema import SNAPSHOT_CUSTOM, TOOL_OUTPUT, USER_REQUEST


def test_default_redaction_happens_before_sqlite_storage(tmp_path):
    db_path = tmp_path / "branchpoint.sqlite"
    bp = BranchPoint(project="demo", db_path=str(db_path))

    with bp.trace("redact") as trace:
        bp.emit(
            type=USER_REQUEST,
            output={
                "password": "correct-horse",
                "headers": {"authorization": "Bearer raw-token"},
                "nested": {"token": "abc123"},
                "safe": "visible",
            },
            metadata={"api_key": "metadata-secret"},
        )

    [event] = bp.store.list_events(trace.run_id)
    assert event.output["password"] == "[REDACTED]"
    assert event.output["headers"]["authorization"] == "[REDACTED]"
    assert event.output["nested"]["token"] == "[REDACTED]"
    assert event.output["safe"] == "visible"
    assert event.metadata["api_key"] == "[REDACTED]"
    assert event.metadata["payload_safety"]["output"]["redaction"]["paths"] == [
        "/headers/authorization",
        "/nested/token",
        "/password",
    ]

    with sqlite3.connect(db_path) as conn:
        output_json, metadata_json = conn.execute("SELECT output_json, metadata_json FROM events").fetchone()
    assert "correct-horse" not in output_json
    assert "raw-token" not in output_json
    assert "metadata-secret" not in metadata_json


def test_custom_pointer_key_regex_and_callback_redaction(tmp_path):
    def callback(path, key, value):
        if key == "callback_secret":
            return True
        return None

    bp = BranchPoint(
        project="demo",
        db_path=str(tmp_path / "branchpoint.sqlite"),
        redaction_rules=("/profile/ssn", "private_key", re.compile(r"sk-live-[A-Za-z0-9]+")),
        redaction_callbacks=(callback,),
        include_default_redaction=False,
    )

    with bp.trace("custom") as trace:
        bp.emit(
            type=USER_REQUEST,
            output={
                "profile": {"ssn": "111-22-3333"},
                "private_key": "key-material",
                "note": "token sk-live-abc123",
                "callback_secret": "callback-value",
                "password": "not-default-without-defaults",
            },
        )

    [event] = bp.store.list_events(trace.run_id)
    assert event.output == {
        "profile": {"ssn": "[REDACTED]"},
        "private_key": "[REDACTED]",
        "note": "[REDACTED]",
        "callback_secret": "[REDACTED]",
        "password": "not-default-without-defaults",
    }
    assert event.metadata["payload_safety"]["output"]["redaction"]["paths"] == [
        "/callback_secret",
        "/note",
        "/private_key",
        "/profile/ssn",
    ]


def test_hashes_are_deterministic_after_redaction(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("hash") as trace:
        first = bp.emit(type=USER_REQUEST, output={"api_key": "first", "stable": {"values": {3, 1, 2}}})
        second = bp.emit(type=USER_REQUEST, output={"api_key": "second", "stable": {"values": {2, 3, 1}}})

    events = bp.store.list_events(trace.run_id)
    assert events[0].output_hash == events[1].output_hash
    assert first.output_hash == second.output_hash


def test_blob_storage_is_redacted_and_validated(tmp_path):
    db_path = tmp_path / ".branchpoint" / "branchpoint.sqlite"
    bp = BranchPoint(project="demo", db_path=str(db_path), max_inline_bytes=32)

    with bp.trace("blob") as trace:
        event = bp.emit(type=USER_REQUEST, output={"api_key": "blob-secret", "text": "x" * 200})

    saved = bp.store.list_events(trace.run_id)[0]
    assert saved.output is None
    assert saved.output_payload_ref is not None
    blob_path = tmp_path / ".branchpoint" / saved.output_payload_ref
    assert "blob-secret" not in blob_path.read_text(encoding="utf-8")
    assert bp.blob_store.get_json(saved.output_payload_ref, expected_hash=saved.output_hash)["api_key"] == "[REDACTED]"
    assert bp.validate_run_blobs(trace.run_id) == []

    blob_path.write_text(json.dumps({"api_key": "[REDACTED]", "text": "corrupted"}), encoding="utf-8")
    problems = bp.validate_run_blobs(trace.run_id)
    assert problems[0]["kind"] == "event"
    assert problems[0]["field"] == "output"
    with pytest.raises(EventContractError):
        bp.blob_store.get_json(saved.output_payload_ref, expected_hash=saved.output_hash)

    blob_path.unlink()
    assert "missing" in bp.validate_run_blobs(trace.run_id)[0]["error"]


def test_payload_truncation_metadata_limits_storage(tmp_path):
    bp = BranchPoint(
        project="demo",
        db_path=str(tmp_path / "branchpoint.sqlite"),
        max_inline_bytes=100_000,
        max_preview_chars=24,
        max_blob_bytes=80,
    )

    with bp.trace("truncate") as trace:
        bp.emit(type=USER_REQUEST, output={"text": "x" * 500})

    [event] = bp.store.list_events(trace.run_id)
    assert event.output["truncated"] is True
    assert event.metadata["payload_safety"]["output"]["truncation"]["truncated"] is True
    assert event.output_hash


def test_snapshot_payloads_and_metadata_are_redacted(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("snapshot") as trace:
        event = bp.emit(type=TOOL_OUTPUT, output={"ok": True})
        snapshot = bp.snapshot(
            kind=SNAPSHOT_CUSTOM,
            event_id=event.event_id,
            payload={"api_key": "snapshot-secret", "answer": 42},
            metadata={"authorization": "Bearer metadata-secret"},
        )

    saved = bp.get_snapshot(snapshot.snapshot_id)
    assert saved.payload == {"api_key": "[REDACTED]", "answer": 42}
    assert saved.metadata["authorization"] == "[REDACTED]"
    assert saved.metadata["redaction"]["paths"] == ["/api_key"]
    assert bp.snapshot_payload(saved) == {"api_key": "[REDACTED]", "answer": 42}


def test_cleanup_removes_rows_and_blobs(tmp_path):
    db_path = tmp_path / ".branchpoint" / "branchpoint.sqlite"
    bp = BranchPoint(project="demo", db_path=str(db_path), max_inline_bytes=32)

    with bp.trace("old") as trace:
        event = bp.emit(type=USER_REQUEST, output={"text": "x" * 200})
        bp.snapshot(kind=SNAPSHOT_CUSTOM, event_id=event.event_id, payload={"text": "y" * 200})

    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE runs SET started_at = ?, ended_at = ? WHERE run_id = ?", (old_time, old_time, trace.run_id))

    run_dir = tmp_path / ".branchpoint" / "runs" / trace.run_id
    assert run_dir.exists()

    result = bp.cleanup(older_than="1d")
    assert result["runs"] == 1
    assert result["events"] == 1
    assert result["snapshots"] == 1
    assert result["blobs_removed"] == 1
    assert bp.store.get_run(trace.run_id) is None
    assert bp.store.list_events(trace.run_id) == []
    assert bp.store.list_snapshots(trace.run_id) == []
    assert not run_dir.exists()


def test_cli_validate_run_and_cleanup(tmp_path, capsys):
    db_path = tmp_path / ".branchpoint" / "branchpoint.sqlite"
    bp = BranchPoint(project="demo", db_path=str(db_path), max_inline_bytes=32)

    with bp.trace("cli") as trace:
        bp.emit(type=USER_REQUEST, output={"text": "x" * 200})

    assert main(["--db", str(db_path), "validate-run", trace.run_id]) == 0
    assert "validation passed" in capsys.readouterr().out

    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE runs SET started_at = ?, ended_at = ? WHERE run_id = ?", (old_time, old_time, trace.run_id))

    assert main(["--db", str(db_path), "cleanup", "--older-than", "1d"]) == 0
    assert "runs=1" in capsys.readouterr().out
