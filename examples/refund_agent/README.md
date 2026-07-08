# Frameworkless Refund Agent Demo

This demo records a tiny, deterministic customer-support refund workflow with
BranchPoint only. It does not use LangGraph, LangChain, CrewAI, PydanticAI, or
any other agent framework.

The fake workflow intentionally fails:

1. A user asks whether customer `C123` is eligible for a refund.
2. A decorated tool returns payment history with `refund_eligible=true`.
3. The demo reads `payment["refund_eligible"]` to preserve field-level provenance.
4. A provenance-preserving prompt is passed into a decorated fake LLM.
5. A decorated memory write stores the fake LLM's incorrect refund status.
6. Manual `finaloutput` and `failurelabel` events close the trace.

## Run It

From the repository root:

```bash
python examples/refund_agent/run_demo.py
```

If you use the repository's `uv` environment:

```bash
uv run python examples/refund_agent/run_demo.py
```

The script prints the run ID, recorded events, provenance details, and graph
edges. For a temporary store during local experiments:

```bash
python examples/refund_agent/run_demo.py --db-path /tmp/branchpoint-refund-demo.sqlite
```

## Expected Shape

The exact IDs vary, but the event list should include:

```text
userrequest initial_request
toolcall get_payment_history
tooloutput get_payment_history
llmcall interpret_payment_history
llmoutput interpret_payment_history
memorywrite write_refund_status
finaloutput final_answer
failurelabel evaluator_result
```

The graph should include these dependency edges:

```text
tooloutput get_payment_history -> llmcall interpret_payment_history
llmoutput interpret_payment_history -> memorywrite write_refund_status
memorywrite write_refund_status -> finaloutput final_answer
finaloutput final_answer -> failurelabel evaluator_result
```

## Provenance Features Shown

- Decorators automatically connect tool outputs to LLM calls and LLM outputs to memory writes.
- Hybrid provenance tracks `payment["refund_eligible"]` with a `["refund_eligible"]` path.
- `bp.prompt().add_json(...).add(..., ref=...)` preserves refs while building prompt text.
- Manual emits use explicit `input_refs` for the final answer and evaluator label.
- Graph edges are built from recorded provenance without a dashboard or framework adapter.

Plain Python f-strings still produce plain strings. Use `bp.prompt()`,
`bp.format(...)`, `bp.depends_on(...)`, or explicit refs when a transformation
would otherwise lose provenance.
