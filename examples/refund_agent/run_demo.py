"""Frameworkless BranchPoint refund-agent provenance demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from branchpoint import BranchPoint  # noqa: E402
from branchpoint.core.schema import FAILURE_LABEL, FINAL_OUTPUT, USER_REQUEST  # noqa: E402


def run_refund_workflow(bp: BranchPoint) -> str:
    """Run the deterministic refund workflow and return the BranchPoint run ID."""
    memory: dict[str, Any] = {}

    @bp.tool("get_payment_history")
    def get_payment_history(customer_id: str) -> dict[str, Any]:
        return {
            "customer_id": customer_id,
            "refund_eligible": True,
            "days_since_purchase": 7,
            "plan": "premium",
        }

    @bp.llm("interpret_payment_history")
    def interpret_payment_history(prompt: Any) -> dict[str, str]:
        return {
            "refund_status": "not_eligible",
            "answer": "The customer is not eligible for a refund.",
            "rationale": "The fake model ignored the refund_eligible field.",
        }

    @bp.memory_write("write_refund_status", exclude_args=[0])
    def write_refund_status(store: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
        stored_status = str(value)
        store[key] = stored_status
        return {"key": key, "stored_status": stored_status}

    with bp.trace("refund-workflow") as trace:
        bp.emit(
            type=USER_REQUEST,
            name="initial_request",
            input={"customer_id": "C123"},
            output={"text": "Is customer C123 eligible for a refund?"},
        )

        payment = get_payment_history("C123")
        eligible = payment["refund_eligible"]
        prompt = (
            bp.prompt()
            .add("Interpret this refund evidence:\n")
            .add_json(payment)
            .add("\nField read refund_eligible=")
            .add(str(eligible), ref=eligible)
            .add("\nReturn a refund status and final answer.")
        )

        interpretation = interpret_payment_history(prompt)
        refund_status = interpretation["refund_status"]
        memory_result = write_refund_status(memory, "refund_status", refund_status)

        final_event = bp.emit(
            type=FINAL_OUTPUT,
            name="final_answer",
            input={"memory_result": bp.detach(memory_result)},
            input_refs=bp.refs(memory_result),
            output={"text": "The customer is not eligible for a refund."},
        )

        bp.emit(
            type=FAILURE_LABEL,
            name="evaluator_result",
            input={"final_answer": final_event.output},
            input_refs=[final_event.event_id],
            output={
                "failed": True,
                "reason": "Customer was eligible, but the final answer denied the refund.",
            },
        )

    return trace.run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frameworkless BranchPoint refund-agent demo.")
    parser.add_argument("--db-path", default=".branchpoint/branchpoint.sqlite", help="SQLite DB path for the demo trace")
    args = parser.parse_args(argv)

    bp = BranchPoint(project="refund-agent-demo", db_path=args.db_path, provenance_mode="hybrid")
    run_id = run_refund_workflow(bp)
    events = bp.store.list_events(run_id)
    graph = bp.graph_builder().build(run_id)

    print(f"Run ID: {run_id}")
    print()
    print_events(events)
    print()
    print_graph(graph, events)
    return 0


def print_events(events: list[Any]) -> None:
    print("Events:")
    for event in events:
        print(f"  {event.type} {event.name or '-'} status={event.status} input_refs={event.input_refs}")
        details = _provenance_details(event)
        if details:
            print(f"    provenance: {_compact_json(details)}")


def print_graph(graph: Any, events: list[Any]) -> None:
    by_id = {event.event_id: event for event in events}
    print("Graph Edges:")
    for source, target, data in sorted(
        graph.edges(data=True),
        key=lambda edge: (_event_label(by_id[edge[0]]), _event_label(by_id[edge[1]]), edge[2]["edge_type"]),
    ):
        print(f"  {_event_label(by_id[source])} -> {_event_label(by_id[target])} [{data['edge_type']}]")
        paths = data.get("metadata", {}).get("paths")
        if paths:
            print(f"    paths: {_compact_json(paths)}")


def _event_label(event: Any) -> str:
    return f"{event.type} {event.name or '-'}"


def _provenance_details(event: Any) -> list[dict[str, Any]]:
    provenance = event.metadata.get("provenance")
    if not isinstance(provenance, dict):
        return []
    details = provenance.get("input_refs_detail")
    return details if isinstance(details, list) else []


def _compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ": "))


if __name__ == "__main__":
    raise SystemExit(main())
