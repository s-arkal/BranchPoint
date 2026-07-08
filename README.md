# BranchPoint

BranchPoint is a small, framework-agnostic Python package for recording trace runs,
trace events, payload blobs, and dependency graphs for agent workflows.

## What It Does

BranchPoint records agent-like workflows as structured events, stores those
events locally, and builds dependency graphs from explicit references between
events. It is designed to work without requiring LangGraph, LangChain, CrewAI,
PydanticAI, or another agent framework.

## Setup

This project uses `uv` for dependency management, command execution, and
lockfile generation.

```bash
uv sync
```

## Basic Usage

```python
from branchpoint import BranchPoint

bp = BranchPoint(project="demo")

with bp.trace("refund-workflow") as trace:
    bp.emit(type="userrequest", name="initial_request", output={"query": "hello"})
```

## Advanced Provenance

BranchPoint can preserve dependencies without an agent framework:

- decorators attach provenance to tool, LLM, retrieval, and memory outputs
- hybrid tracking preserves field reads such as `payment["refund_eligible"]`
- `bp.prompt()`, `bp.format(...)`, and `bp.depends_on(...)` help keep refs through prompt construction
- manual `emit(..., input_refs=...)` remains available for final outputs, labels, and custom events
- the graph builder turns recorded `input_refs` into semantic dependency edges

## CLI

```bash
uv run python -m branchpoint runs
uv run python -m branchpoint events <run_id>
uv run python -m branchpoint graph <run_id>
```

## Demo

Run the frameworkless refund-agent demo:

```bash
python examples/refund_agent/run_demo.py
```

Or, with the repository's `uv` environment:

```bash
uv run python examples/refund_agent/run_demo.py
```

The demo records an intentionally failed refund workflow, then prints the run
ID, events, provenance details, and graph edges. It demonstrates decorator
provenance, field-level reads, prompt ref preservation, and manual final
output/failure label emits.

## Tests

Run tests with:

```bash
uv run pytest
```
