"""Small inspection CLI for BranchPoint traces."""

from __future__ import annotations

import argparse

from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.sdk.client import BranchPoint
from branchpoint.storage.sqlite_store import SQLiteEventStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="branchpoint")
    parser.add_argument("--db", default=".branchpoint/branchpoint.sqlite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("runs")
    events_parser = subparsers.add_parser("events")
    events_parser.add_argument("run_id")
    graph_parser = subparsers.add_parser("graph")
    graph_parser.add_argument("run_id")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--older-than", required=True, help="Delete completed runs older than a duration like 30d, 12h, 10m, or 60s")
    validate_parser = subparsers.add_parser("validate-run")
    validate_parser.add_argument("run_id")

    args = parser.parse_args(argv)
    store = SQLiteEventStore(db_path=args.db)

    if args.command == "runs":
        print("Runs:")
        for run in store.list_runs():
            print(f"  {run.run_id}  {run.name or '-'}  {run.status}  {run.started_at}")
        return 0

    if args.command == "events":
        print(f"Events for {args.run_id}:")
        for index, event in enumerate(store.list_events(args.run_id), start=1):
            print(f"  N{index} {event.type} {event.name or '-'} {event.status}")
        return 0

    if args.command == "graph":
        graph = GraphBuilder(store).build(args.run_id)
        print(f"Graph for {args.run_id}:")
        aliases = {node: f"N{index}" for index, node in enumerate(graph.nodes, start=1)}
        for source, target, data in graph.edges(data=True):
            print(f"  {aliases[source]} -> {aliases[target]} {data['edge_type']}")
        return 0

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
        problems = bp.validate_run_blobs(args.run_id)
        if not problems:
            print(f"Run {args.run_id} blob validation passed")
            return 0
        print(f"Run {args.run_id} blob validation failed:")
        for problem in problems:
            print(f"  {problem['kind']} {problem['id']} {problem['field']} {problem['ref']}: {problem['error']}")
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
