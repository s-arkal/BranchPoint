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

## CLI

```bash
uv run python -m branchpoint runs
uv run python -m branchpoint events <run_id>
uv run python -m branchpoint graph <run_id>
```

## Demo

Run the frameworkless refund-agent demo:

```bash
uv run python examples/refund_agent/run_demo.py
```

The demo records an intentionally failed refund workflow and writes a Mermaid
graph viewer under `.branchpoint/graphs/`.

To generate the graph files without opening a browser:

```bash
uv run python examples/refund_agent/run_demo.py --no-open
```

## Tests

Run tests with:

```bash
uv run pytest
```
