"""Stable ID generation helpers."""

from __future__ import annotations

import uuid


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def new_span_id() -> str:
    return f"span_{uuid.uuid4().hex}"


def new_edge_id() -> str:
    return f"edge_{uuid.uuid4().hex}"
