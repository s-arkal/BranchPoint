"""Small inspection CLI for BranchPoint traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.sdk.client import BranchPoint
from branchpoint.storage.sqlite_store import SQLiteEventStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="branchpoint")
    parser.add_argument("--db", default=".branchpoint/branchpoint.sqlite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    runs_parser = subparsers.add_parser("runs")
    _add_json_options(runs_parser)
    events_parser = subparsers.add_parser("events")
    events_parser.add_argument("run_id")
    _add_json_options(events_parser)
    event_parser = subparsers.add_parser("event")
    event_parser.add_argument("event_id")
    _add_json_options(event_parser)
    payload_parser = subparsers.add_parser("payload")
    payload_parser.add_argument("event_id")
    payload_field = payload_parser.add_mutually_exclusive_group(required=True)
    payload_field.add_argument("--input", action="store_const", const="input", dest="field")
    payload_field.add_argument("--output", action="store_const", const="output", dest="field")
    _add_json_options(payload_parser)
    graph_parser = subparsers.add_parser("graph")
    graph_parser.add_argument("run_id")
    _add_json_options(graph_parser)
    graph_parser.add_argument("--output", help="Write graph JSON to a file")
    validate_graph_parser = subparsers.add_parser("validate-graph")
    validate_graph_parser.add_argument("run_id")
    _add_json_options(validate_graph_parser)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--older-than", required=True, help="Delete completed runs older than a duration like 30d, 12h, 10m, or 60s")
    validate_parser = subparsers.add_parser("validate-run")
    validate_parser.add_argument("run_id")
    _add_json_options(validate_parser)
    snapshots_parser = subparsers.add_parser("snapshots")
    snapshots_parser.add_argument("run_id")
    snapshots_parser.add_argument("--event-id")
    snapshots_parser.add_argument("--kind")
    _add_json_options(snapshots_parser)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("snapshot_id")
    snapshot_parser.add_argument("--payload", action="store_true", help="Include the full redacted snapshot payload")
    _add_json_options(snapshot_parser)

    args = parser.parse_args(argv)
    store = SQLiteEventStore(db_path=args.db)

    if args.command == "runs":
        runs = store.list_runs()
        if _wants_json(args):
            return _emit_json([_run_to_dict(run) for run in runs], args)
        print("Runs:")
        for run in runs:
            print(f"  {run.run_id}  {run.name or '-'}  {run.status}  {run.started_at}")
        return 0

    if args.command == "events":
        events = store.list_events(args.run_id)
        if _wants_json(args):
            return _emit_json([_event_to_dict(event) for event in events], args)
        print(f"Events for {args.run_id}:")
        for index, event in enumerate(events, start=1):
            print(f"  N{index} {event.type} {event.name or '-'} {event.status}")
        return 0

    if args.command == "event":
        event = store.get_event(args.event_id)
        if event is None:
            print(f"Event {args.event_id} not found")
            return 1
        if _wants_json(args):
            return _emit_json(_event_to_dict(event), args)
        print(f"Event {event.event_id}:")
        print(f"  run_id: {event.run_id}")
        print(f"  type: {event.type}")
        print(f"  name: {event.name or '-'}")
        print(f"  status: {event.status}")
        print(f"  parent_id: {event.parent_id or '-'}")
        print(f"  input_refs: {', '.join(event.input_refs) if event.input_refs else '-'}")
        print(f"  output_refs: {', '.join(event.output_refs) if event.output_refs else '-'}")
        return 0

    if args.command == "payload":
        bp = BranchPoint(project="_inspection", db_path=args.db)
        try:
            payload = bp.event_payload(args.event_id, args.field)
        except Exception as exc:
            print(str(exc))
            return 1
        return _emit_json(payload, args, force_stdout=True)

    if args.command == "graph":
        builder = GraphBuilder(store)
        if _wants_json(args) or args.output:
            graph_json = builder.export_json(args.run_id)
            if args.output:
                Path(args.output).write_text(json.dumps(graph_json, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                return 0
            return _emit_json(graph_json, args)
        graph = builder.build(args.run_id)
        print(f"Graph for {args.run_id}:")
        aliases = {node: f"N{index}" for index, node in enumerate(graph.nodes, start=1)}
        for source, target, data in graph.edges(data=True):
            print(f"  {aliases[source]} -> {aliases[target]} {data['edge_type']}")
        return 0

    if args.command == "validate-graph":
        report = GraphBuilder(store).validate_graph(args.run_id)
        if _wants_json(args):
            _emit_json(report, args)
        else:
            _print_validation_report("Graph", report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "cleanup":
        bp = BranchPoint(project="_inspection", db_path=args.db)
        result = bp.cleanup(older_than=args.older_than)
        print(
            "Cleanup:"
            f" runs={result['runs']}"
            f" events={result['events']}"
            f" edges={result['edges']}"
            f" snapshots={result['snapshots']}"
            f" blobs_removed={result['blobs_removed']}"
        )
        return 0

    if args.command == "validate-run":
        bp = BranchPoint(project="_inspection", db_path=args.db)
        report = bp.validate_run(args.run_id)
        if _wants_json(args):
            _emit_json(report, args)
        else:
            _print_validation_report("Run", report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "snapshots":
        snapshots = store.list_snapshots(args.run_id, event_id=args.event_id, kind=args.kind)
        if _wants_json(args):
            return _emit_json([_snapshot_to_dict(snapshot) for snapshot in snapshots], args)
        print(f"Snapshots for {args.run_id}:")
        for index, snapshot in enumerate(snapshots, start=1):
            event_id = snapshot.event_id or "-"
            print(f"  S{index} {snapshot.snapshot_id} {snapshot.kind} event={event_id} {snapshot.name or '-'}")
        return 0

    if args.command == "snapshot":
        bp = BranchPoint(project="_inspection", db_path=args.db)
        snapshot = store.get_snapshot(args.snapshot_id)
        if snapshot is None:
            print(f"Snapshot {args.snapshot_id} not found")
            return 1
        payload = bp.snapshot_payload(snapshot) if args.payload else None
        rendered = _snapshot_to_dict(snapshot, payload=payload)
        if _wants_json(args) or args.payload:
            return _emit_json(rendered, args)
        print(f"Snapshot {snapshot.snapshot_id}:")
        print(f"  run_id: {snapshot.run_id}")
        print(f"  event_id: {snapshot.event_id or '-'}")
        print(f"  kind: {snapshot.kind}")
        print(f"  name: {snapshot.name or '-'}")
        print(f"  payload_ref: {snapshot.payload_ref or '-'}")
        print(f"  payload_hash: {snapshot.payload_hash or '-'}")
        return 0

    return 1


def _add_json_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--format", choices=("pretty", "json"), default="pretty")


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "format", "pretty") == "json")


def _emit_json(value: Any, args: argparse.Namespace, *, force_stdout: bool = False) -> int:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    output = getattr(args, "output", None)
    if output and not force_stdout:
        Path(output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


def _print_validation_report(label: str, report: dict[str, Any]) -> None:
    if report["status"] == "pass":
        print(f"{label} {report['run_id']} validation passed")
        return
    print(f"{label} {report['run_id']} validation failed:")
    for error in report["errors"]:
        print(f"  ERROR {error['code']}: {error['message']}")
    for warning in report["warnings"]:
        print(f"  WARN {warning['code']}: {warning['message']}")


def _run_to_dict(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "name": run.name,
        "schema_version": run.schema_version,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "failure_label": run.failure_label,
        "metadata": run.metadata,
    }


def _event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "project_id": event.project_id,
        "schema_version": event.schema_version,
        "type": event.type,
        "name": event.name,
        "parent_id": event.parent_id,
        "span_id": event.span_id,
        "timestamp_start": event.timestamp_start,
        "timestamp_end": event.timestamp_end,
        "status": event.status,
        "input": event.input,
        "output": event.output,
        "input_refs": event.input_refs,
        "output_refs": event.output_refs,
        "metadata": event.metadata,
        "input_payload_ref": event.input_payload_ref,
        "output_payload_ref": event.output_payload_ref,
        "input_hash": event.input_hash,
        "output_hash": event.output_hash,
    }


def _snapshot_to_dict(snapshot: Any, *, payload: Any = None) -> dict[str, Any]:
    rendered = {
        "snapshot_id": snapshot.snapshot_id,
        "run_id": snapshot.run_id,
        "event_id": snapshot.event_id,
        "project_id": snapshot.project_id,
        "schema_version": snapshot.schema_version,
        "kind": snapshot.kind,
        "name": snapshot.name,
        "timestamp": snapshot.timestamp,
        "payload": snapshot.payload,
        "payload_ref": snapshot.payload_ref,
        "payload_hash": snapshot.payload_hash,
        "preview": snapshot.preview,
        "metadata": snapshot.metadata,
    }
    if payload is not None:
        rendered["payload"] = payload
    return rendered


if __name__ == "__main__":
    raise SystemExit(main())
