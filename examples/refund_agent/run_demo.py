"""Frameworkless BranchPoint refund-agent demo."""

from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from branchpoint import BranchPoint  # noqa: E402
from branchpoint.core.schema import (  # noqa: E402
    FAILURE_LABEL,
    FINAL_OUTPUT,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_WRITE,
    TOOL_CALL,
    TOOL_OUTPUT,
    USER_REQUEST,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frameworkless BranchPoint refund-agent demo.")
    parser.add_argument("--no-open", action="store_true", help="write the Mermaid graph without opening it")
    args = parser.parse_args(argv)

    bp = BranchPoint(project="refund-agent-demo")

    with bp.trace("refund-workflow") as trace:
        user_request_event = bp.emit(
            type=USER_REQUEST,
            name="initial_request",
            input={"customer_id": "C123"},
            output={"text": "Is customer C123 eligible for a refund?"},
        )

        tool_call_event = bp.emit(
            type=TOOL_CALL,
            name="get_payment_history",
            input={"customer_id": "C123"},
            input_refs=[user_request_event.event_id],
        )

        tool_output_event = bp.emit(
            type=TOOL_OUTPUT,
            name="get_payment_history",
            input_refs=[tool_call_event.event_id],
            output={
                "customer_id": "C123",
                "refund_eligible": True,
                "days_since_purchase": 7,
                "plan": "premium",
            },
        )

        llm_call_event = bp.emit(
            type=LLM_CALL,
            name="interpret_payment_history",
            input={
                "instruction": "Interpret the payment history and decide refund eligibility.",
                "payment_history": tool_output_event.output,
            },
            input_refs=[tool_output_event.event_id],
        )

        llm_output_event = bp.emit(
            type=LLM_OUTPUT,
            name="interpret_payment_history",
            input_refs=[llm_call_event.event_id, tool_output_event.event_id],
            output={"text": "The customer is not eligible for a refund."},
        )

        memory_write_event = bp.emit(
            type=MEMORY_WRITE,
            name="write_refund_status",
            input_refs=[llm_output_event.event_id],
            input={"key": "refund_status", "value": "not_eligible"},
            output={"key": "refund_status", "value": "not_eligible"},
            metadata={"memory_key": "refund_status"},
        )

        final_output_event = bp.emit(
            type=FINAL_OUTPUT,
            name="final_answer",
            input_refs=[memory_write_event.event_id, llm_output_event.event_id],
            output={"text": "The customer is not eligible for a refund."},
        )

        bp.emit(
            type=FAILURE_LABEL,
            name="evaluator_result",
            input_refs=[final_output_event.event_id],
            output={
                "failed": True,
                "reason": "Customer was eligible, but agent denied refund.",
            },
        )

    graph = bp.graph_builder().build(trace.run_id)
    events = bp.store.list_events(trace.run_id)
    aliases = {event.event_id: f"N{index}" for index, event in enumerate(events, start=1)}
    mermaid_path, viewer_path = _write_mermaid_graph(trace.run_id, events, aliases)

    print(f"Created BranchPoint run: {trace.run_id}")
    print()
    print("Events:")
    for event in events:
        print(f"  {aliases[event.event_id]} {event.type} {event.name or '-'}")
        _print_payload("input", event.input)
        _print_payload("output", event.output)

    print()
    print("Edges:")
    for source, target, data in graph.edges(data=True):
        print(f"  {aliases[source]} -> {aliases[target]} {data['edge_type']}")

    print()
    print(f"Mermaid graph source: {mermaid_path}")
    print(f"Mermaid graph viewer: {viewer_path}")
    if not args.no_open:
        if webbrowser.open(viewer_path.as_uri()):
            print("Opened Mermaid graph viewer in your default browser.")
        else:
            print("Open the Mermaid viewer path above to view the graph.")

    return 0


def _write_mermaid_graph(run_id: str, events: list[Any], aliases: dict[str, str]) -> tuple[Path, Path]:
    graph_dir = PROJECT_ROOT / ".branchpoint" / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    mermaid_path = graph_dir / f"{run_id}.mmd"
    viewer_path = graph_dir / f"{run_id}.html"
    mermaid_source = _render_mermaid(run_id, events, aliases)

    mermaid_path.write_text(mermaid_source, encoding="utf-8")
    viewer_path.write_text(_render_mermaid_html(run_id, mermaid_source), encoding="utf-8")
    return mermaid_path, viewer_path


def _render_mermaid(run_id: str, events: list[Any], aliases: dict[str, str]) -> str:
    by_type = {event.type: event for event in events}

    def node(event_type: str) -> str:
        event = by_type[event_type]
        return aliases[event.event_id]

    return f"""---
title: BranchPoint refund-agent dependency graph
---
flowchart TD
    run[\"run: {run_id}\"]

    subgraph evidence[\"Request and tool evidence\"]
{labels_for_group(events, aliases, {USER_REQUEST, TOOL_CALL, TOOL_OUTPUT})}
    end

    subgraph interpretation[\"Agent interpretation\"]
{labels_for_group(events, aliases, {LLM_CALL, LLM_OUTPUT})}
    end

    subgraph outcome[\"State, answer, and evaluation\"]
{labels_for_group(events, aliases, {MEMORY_WRITE, FINAL_OUTPUT, FAILURE_LABEL})}
    end

    run -. records .-> {node(USER_REQUEST)}
    {node(USER_REQUEST)} -->|asks about C123| {node(TOOL_CALL)}
    {node(TOOL_CALL)} -->|tool executes| {node(TOOL_OUTPUT)}
    {node(TOOL_OUTPUT)} -->|refund_eligible=true| {node(LLM_CALL)}
    {node(LLM_CALL)} -->|prompt to fake LLM| {node(LLM_OUTPUT)}
    {node(TOOL_OUTPUT)} -.->|evidence contradicted| {node(LLM_OUTPUT)}
    {node(LLM_OUTPUT)} -->|stores bad interpretation| {node(MEMORY_WRITE)}
    {node(MEMORY_WRITE)} -->|state used for answer| {node(FINAL_OUTPUT)}
    {node(LLM_OUTPUT)} -.->|also informs answer| {node(FINAL_OUTPUT)}
    {node(TOOL_OUTPUT)} -.->|ground truth: eligible| {node(FAILURE_LABEL)}
    {node(FINAL_OUTPUT)} -->|denied refund| {node(FAILURE_LABEL)}

    classDef request fill:#dbeafe,stroke:#2563eb,color:#111827
    classDef tool fill:#dcfce7,stroke:#16a34a,color:#111827
    classDef llm fill:#ede9fe,stroke:#7c3aed,color:#111827
    classDef state fill:#fef3c7,stroke:#d97706,color:#111827
    classDef failure fill:#fee2e2,stroke:#dc2626,color:#111827
    classDef run fill:#f8fafc,stroke:#475569,color:#111827

    class run run
    class {node(USER_REQUEST)} request
    class {node(TOOL_CALL)},{node(TOOL_OUTPUT)} tool
    class {node(LLM_CALL)},{node(LLM_OUTPUT)} llm
    class {node(MEMORY_WRITE)} state
    class {node(FINAL_OUTPUT)},{node(FAILURE_LABEL)} failure
"""


def labels_for_group(events: list[Any], aliases: dict[str, str], event_types: set[str]) -> str:
    return "\n".join(
        f"        {aliases[event.event_id]}[\"{_mermaid_label(event, aliases[event.event_id])}\"]"
        for event in events
        if event.type in event_types
    )


def _mermaid_label(event: Any, alias: str) -> str:
    return f"{alias} {event.type}<br/>{event.name or '-'}<br/>{_mermaid_summary(event)}"


def _mermaid_summary(event: Any) -> str:
    if event.type == USER_REQUEST:
        return "C123 refund question"
    if event.type == TOOL_CALL:
        return "get payment history"
    if event.type == TOOL_OUTPUT:
        return "refund_eligible=true"
    if event.type == LLM_CALL:
        return "interpret evidence"
    if event.type == LLM_OUTPUT:
        return "incorrectly denies refund"
    if event.type == MEMORY_WRITE:
        return "refund_status=not_eligible"
    if event.type == FINAL_OUTPUT:
        return "final answer denies"
    if event.type == FAILURE_LABEL:
        return "failed=true"
    return "recorded event"


def _render_mermaid_html(run_id: str, mermaid_source: str) -> str:
    escaped_run_id = html.escape(run_id)
    escaped_mermaid = html.escape(mermaid_source)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BranchPoint Mermaid Graph - {escaped_run_id}</title>
  <style>
    body {{
      margin: 0;
      padding: 28px;
      background: #f8fafc;
      color: #111827;
      font-family: Arial, sans-serif;
    }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    p {{ margin: 0 0 22px; color: #475569; }}
    .wrap {{
      overflow: auto;
      background: white;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 18px;
    }}
    details {{ margin-top: 18px; }}
    pre {{
      overflow: auto;
      background: #0f172a;
      color: #e2e8f0;
      border-radius: 8px;
      padding: 14px;
    }}
  </style>
</head>
<body>
  <h1>BranchPoint refund-agent dependency graph</h1>
  <p>Run {escaped_run_id}</p>
  <div class="wrap">
    <pre class="mermaid">
{escaped_mermaid}
    </pre>
  </div>
  <details>
    <summary>Mermaid source</summary>
    <pre>{escaped_mermaid}</pre>
  </details>
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
    mermaid.initialize({{ startOnLoad: true, theme: "base", flowchart: {{ curve: "basis" }} }});
  </script>
</body>
</html>
"""


def _print_payload(label: str, payload: Any) -> None:
    if payload is not None:
        print(f"     {label}: {_compact_json(payload)}")


def _compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ": "))


if __name__ == "__main__":
    raise SystemExit(main())
