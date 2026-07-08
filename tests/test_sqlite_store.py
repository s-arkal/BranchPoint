import sqlite3

from branchpoint.core.graph_types import GraphEdge
from branchpoint.core.ids import new_event_id, new_run_id
from branchpoint.core.schema import SUCCESS, TraceEvent, TraceRun, USER_REQUEST, utc_now_iso
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

    store.create_run(run)
    store.append_event(event)
    store.append_edge(edge)
    store.finish_run(run.run_id, SUCCESS)

    assert store.get_run(run.run_id).status == SUCCESS
    assert store.list_events(run.run_id)[0].output == {"query": "hello"}
    assert store.list_edges(run.run_id)[0].edge_type == "controlflow"

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(events)").fetchall()}

    assert {"input_json", "output_json", "input_payload_ref", "output_hash"} <= columns
    assert {"idx_events_run_id", "idx_events_type", "idx_events_parent_id"} <= indexes
