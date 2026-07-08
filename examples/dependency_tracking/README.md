# BranchPoint Dependency-Tracking Examples

These examples demonstrate the advanced BranchPoint dependency-tracking APIs with small deterministic programs. They do not call real APIs or LLMs.

They cover:

1. automatic decorator provenance
2. `bp.refs(...)`
3. `bp.depends_on(...)`
4. `bp.prompt()`
5. `bp.format(...)`
6. field-level proxy tracking
7. manual `emit` with `auto_refs`
8. reserved kwargs such as `bp_input_refs` and `bp_depends_on`
9. graph edges generated from inferred `input_refs`

## Run Everything

```bash
python examples/dependency_tracking/run_all.py
```

## Run Individual Examples

```bash
python examples/dependency_tracking/01_auto_decorator_provenance.py
python examples/dependency_tracking/02_depends_on_prompt_string.py
python examples/dependency_tracking/03_prompt_builder.py
python examples/dependency_tracking/04_field_level_proxy.py
python examples/dependency_tracking/05_manual_emit_and_refs.py
python examples/dependency_tracking/06_reserved_kwargs.py
```

Each script starts one trace, records events, builds the graph, prints readable event aliases, prints `input_refs`, prints graph edges, and asserts the key dependency edges. Missing dependencies fail loudly.

## What Each Example Proves

`01_auto_decorator_provenance.py` shows decorated outputs becoming dependencies automatically: `tooloutput get_payment_history -> llmcall interpret_payment_history` and `llmoutput interpret_payment_history -> memorywrite write_refund_status`.

`02_depends_on_prompt_string.py` shows `bp.depends_on(...)` preserving dependencies when a normal f-string would otherwise lose provenance.

`03_prompt_builder.py` shows `bp.prompt()` and `bp.format(...)` building clean printable prompts while carrying refs into an LLM call.

`04_field_level_proxy.py` shows field reads such as `payment["refund_eligible"]` and `payment["days_since_purchase"]` carrying provenance, including path details when available.

`05_manual_emit_and_refs.py` shows `bp.refs(...)` wiring a manual `finaloutput` event to an `llmoutput`, then wiring a `failurelabel` to the final output.

`06_reserved_kwargs.py` shows `bp_depends_on` and `bp_input_refs` controlling dependencies without being passed into the wrapped function.

## API Guidance

Decorators are the normal high-level API.
Provenance lets decorated outputs become dependencies automatically.
`bp.depends_on` is for cases where Python loses provenance, especially strings.
`bp.prompt` is for provenance-preserving prompt construction.
`bp.refs` is for manually wiring events without manually tracking event IDs.
`emit` is the low-level escape hatch for one-off events like `finaloutput` and `failurelabel`.
