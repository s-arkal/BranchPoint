"""Small inspection CLI for BranchPoint traces."""

from __future__ import annotations

import argparse

from branchpoint.core.graph_builder import GraphBuilder
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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
