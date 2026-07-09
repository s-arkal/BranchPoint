"""SQLite-backed structured event storage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from branchpoint.core.graph_types import GraphEdge
from branchpoint.core.schema import (
    RUNNING,
    SCHEMA_VERSION,
    TraceEvent,
    TraceRun,
    validate_event_contract,
    validate_schema_version,
    validate_status,
)
from branchpoint.core.serialization import safe_serialize


class SQLiteEventStore:
    def __init__(self, db_path: str | Path = ".branchpoint/branchpoint.sqlite", *, strict_event_types: bool = True) -> None:
        self.db_path = Path(db_path)
        self.strict_event_types = strict_event_types
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT,
                    schema_version TEXT NOT NULL DEFAULT 'v1',
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
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    schema_version TEXT NOT NULL DEFAULT 'v1',
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
                    output_hash TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    target_event_id TEXT NOT NULL,
                    schema_version TEXT NOT NULL DEFAULT 'v1',
                    edge_type TEXT NOT NULL,
                    weight REAL NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT,
                    metadata_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )
            self._ensure_column(conn, "runs", "schema_version", f"TEXT NOT NULL DEFAULT '{SCHEMA_VERSION}'")
            self._ensure_column(conn, "events", "schema_version", f"TEXT NOT NULL DEFAULT '{SCHEMA_VERSION}'")
            self._ensure_column(conn, "graph_edges", "schema_version", f"TEXT NOT NULL DEFAULT '{SCHEMA_VERSION}'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_parent_id ON events(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_run_id ON graph_edges(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_event_id)")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_run(self, run: TraceRun) -> None:
        validate_schema_version(run.schema_version)
        validate_status(run.status)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, project_id, name, schema_version, status, started_at,
                    ended_at, failure_label, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.project_id,
                    run.name,
                    run.schema_version,
                    run.status,
                    run.started_at,
                    run.ended_at,
                    run.failure_label,
                    _json(run.metadata),
                ),
            )

    def finish_run(self, run_id: str, status: str, failure_label: str | None = None) -> None:
        validate_status(status)
        ended_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?, failure_label = COALESCE(?, failure_label) WHERE run_id = ?",
                (status, ended_at, failure_label, run_id),
            )

    def get_run(self, run_id: str) -> TraceRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row) if row else None

    def list_runs(self) -> list[TraceRun]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
        return [_run_from_row(row) for row in rows]

    def append_event(self, event: TraceEvent) -> None:
        validate_event_contract(
            event.type,
            event.status,
            event.metadata,
            strict_event_types=self.strict_event_types,
            schema_version=event.schema_version,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    event_id, run_id, project_id, schema_version, type, name, parent_id,
                    span_id, timestamp_start, timestamp_end, status, input_json,
                    output_json, input_refs_json, output_refs_json, metadata_json,
                    input_payload_ref, output_payload_ref, input_hash, output_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.project_id,
                    event.schema_version,
                    event.type,
                    event.name,
                    event.parent_id,
                    event.span_id,
                    event.timestamp_start,
                    event.timestamp_end,
                    event.status,
                    _json_or_none(event.input),
                    _json_or_none(event.output),
                    _json(event.input_refs),
                    _json(event.output_refs),
                    _json(event.metadata),
                    event.input_payload_ref,
                    event.output_payload_ref,
                    event.input_hash,
                    event.output_hash,
                ),
            )

    def list_events(self, run_id: str) -> list[TraceEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY timestamp_start ASC, event_id ASC",
                (run_id,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def append_edge(self, edge: GraphEdge) -> None:
        validate_schema_version(edge.schema_version)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO graph_edges (
                    edge_id, run_id, source_event_id, target_event_id,
                    schema_version, edge_type, weight, confidence, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.edge_id,
                    edge.run_id,
                    edge.source_event_id,
                    edge.target_event_id,
                    edge.schema_version,
                    edge.edge_type,
                    edge.weight,
                    edge.confidence,
                    edge.reason,
                    _json(edge.metadata),
                ),
            )

    def list_edges(self, run_id: str) -> list[GraphEdge]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE run_id = ? ORDER BY edge_type ASC, edge_id ASC",
                (run_id,),
            ).fetchall()
        return [_edge_from_row(row) for row in rows]


def _json(value: object) -> str:
    return json.dumps(safe_serialize(value), sort_keys=True)


def _json_or_none(value: object) -> str | None:
    return None if value is None else _json(value)


def _loads(value: str | None, default: object) -> object:
    return default if value is None else json.loads(value)


def _run_from_row(row: sqlite3.Row) -> TraceRun:
    return TraceRun(
        run_id=row["run_id"],
        project_id=row["project_id"],
        name=row["name"],
        started_at=row["started_at"],
        schema_version=row["schema_version"] or SCHEMA_VERSION,
        ended_at=row["ended_at"],
        status=row["status"] or RUNNING,
        failure_label=row["failure_label"],
        metadata=dict(_loads(row["metadata_json"], {})),
    )


def _event_from_row(row: sqlite3.Row) -> TraceEvent:
    return TraceEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        project_id=row["project_id"],
        type=row["type"],
        schema_version=row["schema_version"] or SCHEMA_VERSION,
        name=row["name"],
        parent_id=row["parent_id"],
        span_id=row["span_id"],
        timestamp_start=row["timestamp_start"],
        timestamp_end=row["timestamp_end"],
        input=_loads(row["input_json"], None),
        output=_loads(row["output_json"], None),
        input_refs=list(_loads(row["input_refs_json"], [])),
        output_refs=list(_loads(row["output_refs_json"], [])),
        status=row["status"],
        metadata=dict(_loads(row["metadata_json"], {})),
        input_payload_ref=row["input_payload_ref"],
        output_payload_ref=row["output_payload_ref"],
        input_hash=row["input_hash"],
        output_hash=row["output_hash"],
    )


def _edge_from_row(row: sqlite3.Row) -> GraphEdge:
    return GraphEdge(
        edge_id=row["edge_id"],
        run_id=row["run_id"],
        source_event_id=row["source_event_id"],
        target_event_id=row["target_event_id"],
        edge_type=row["edge_type"],
        schema_version=row["schema_version"] or SCHEMA_VERSION,
        weight=row["weight"],
        confidence=row["confidence"],
        reason=row["reason"],
        metadata=dict(_loads(row["metadata_json"], {})),
    )
