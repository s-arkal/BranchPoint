import sqlite3

from branchpoint.core.graph_types import GraphBuild, GraphEdge
from branchpoint.core.ids import new_event_id, new_run_id, new_snapshot_id
from branchpoint.core.schema import (
    SCHEMA_VERSION,
    SNAPSHOT_CUSTOM,
    SUCCESS,
    Snapshot,
    TraceEvent,
    TraceRun,
    USER_REQUEST,
    utc_now_iso,
)
from branchpoint.storage.sqlite_store import SQLiteEventStore


def test_sqlite_store_persists_runs_events_edges_and_schema_indexes(tmp_path):
    db_path = tmp_path / "branchpoint.sqlite"
    store = SQLiteEventStore(db_path=db_path)
    run = TraceRun(run_id=new_run_id(), project_id="demo", name="workflow", started_at=utc_now_iso())
    event = TraceEvent(
        event_id=new_event_id(),
        run_id=run.run_id,
        project_id=run.project_id,
        type=USER_REQUEST,
        output={"query": "hello"},
    )
    edge = GraphEdge(
        edge_id="edge_test",
        run_id=run.run_id,
        source_event_id=event.event_id,
        target_event_id=event.event_id,
        edge_type="controlflow",
    )
    build = GraphBuild(
        build_id="gbuild_test",
        run_id=run.run_id,
        metadata={"event_count": 1, "returned_edge_count": 1},
    )
    snapshot = Snapshot(
        snapshot_id=new_snapshot_id(),
        run_id=run.run_id,
        event_id=event.event_id,
        project_id=run.project_id,
        kind=SNAPSHOT_CUSTOM,
        payload={"query": "hello"},
        payload_hash="hash_test",
        preview={"query": "hello"},
    )

    store.create_run(run)
    store.append_event(event)
    store.append_edge(edge)
    store.append_graph_build(build)
    store.append_snapshot(snapshot)
    store.finish_run(run.run_id, SUCCESS)

    assert store.get_run(run.run_id).status == SUCCESS
    assert store.get_run(run.run_id).schema_version == SCHEMA_VERSION
    assert store.list_events(run.run_id)[0].output == {"query": "hello"}
    assert store.list_events(run.run_id)[0].schema_version == SCHEMA_VERSION
    assert store.list_edges(run.run_id)[0].edge_type == "controlflow"
    assert store.list_edges(run.run_id)[0].schema_version == SCHEMA_VERSION
    assert store.list_graph_builds(run.run_id)[0].build_id == "gbuild_test"
    assert store.list_graph_builds(run.run_id)[0].metadata["event_count"] == 1
    assert store.get_snapshot(snapshot.snapshot_id).payload == {"query": "hello"}
    assert store.get_snapshot(snapshot.snapshot_id).schema_version == SCHEMA_VERSION
    assert store.list_snapshots(run.run_id, event_id=event.event_id, kind=SNAPSHOT_CUSTOM)[0].snapshot_id == snapshot.snapshot_id

    with sqlite3.connect(db_path) as conn:
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        edge_columns = {row[1] for row in conn.execute("PRAGMA table_info(graph_edges)").fetchall()}
        graph_build_columns = {row[1] for row in conn.execute("PRAGMA table_info(graph_builds)").fetchall()}
        snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(events)").fetchall()}
        graph_build_indexes = {row[1] for row in conn.execute("PRAGMA index_list(graph_builds)").fetchall()}
        snapshot_indexes = {row[1] for row in conn.execute("PRAGMA index_list(snapshots)").fetchall()}

    assert "schema_version" in run_columns
    assert {"schema_version", "input_json", "output_json", "input_payload_ref", "output_hash"} <= event_columns
    assert "schema_version" in edge_columns
    assert {"build_id", "run_id", "builder_version", "rule_version", "created_at", "status", "metadata_json"} <= graph_build_columns
    assert {"schema_version", "payload_json", "payload_ref", "payload_hash", "preview_json"} <= snapshot_columns
    assert {"idx_events_run_id", "idx_events_type", "idx_events_parent_id"} <= indexes
    assert "idx_graph_builds_run_id" in graph_build_indexes
    assert {"idx_snapshots_run_id", "idx_snapshots_event_id", "idx_snapshots_kind"} <= snapshot_indexes


def test_sqlite_store_hydrates_old_rows_without_schema_version(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    run_id = new_run_id()
    event_id = new_event_id()
    edge_id = "edge_legacy"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                failure_label TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                type TEXT NOT NULL,
                name TEXT,
                parent_id TEXT,
                span_id TEXT,
                timestamp_start TEXT NOT NULL,
                timestamp_end TEXT,
                status TEXT NOT NULL,
                input_json TEXT,
                output_json TEXT,
                input_refs_json TEXT,
                output_refs_json TEXT,
                metadata_json TEXT,
                input_payload_ref TEXT,
                output_payload_ref TEXT,
                input_hash TEXT,
                output_hash TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE graph_edges (
                edge_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                target_event_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                weight REAL NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, "demo", "legacy", SUCCESS, utc_now_iso(), None, None, "{}"),
        )
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                run_id,
                "demo",
                USER_REQUEST,
                "legacy_event",
                None,
                None,
                utc_now_iso(),
                None,
                SUCCESS,
                None,
                '{"query": "hello"}',
                "[]",
                "[]",
                "{}",
                None,
                None,
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (edge_id, run_id, event_id, event_id, "controlflow", 0.4, 1.0, None, "{}"),
        )

    store = SQLiteEventStore(db_path=db_path)

    assert store.get_run(run_id).schema_version == SCHEMA_VERSION
    assert store.list_events(run_id)[0].schema_version == SCHEMA_VERSION
    assert store.list_edges(run_id)[0].schema_version == SCHEMA_VERSION
