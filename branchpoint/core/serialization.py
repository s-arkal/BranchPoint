"""Safe JSON-compatible serialization."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any


def safe_serialize(value: Any) -> Any:
    try:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        branchpoint_serialize = getattr(value, "__branchpoint_serialize__", None)
        if callable(branchpoint_serialize):
            return safe_serialize(branchpoint_serialize())
        if isinstance(value, bytes):
            return {"type": "bytes", "length": len(value)}
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Exception):
            return {"error_type": type(value).__name__, "error": str(value)}
        if is_dataclass(value) and not isinstance(value, type):
            return safe_serialize(asdict(value))
        if hasattr(value, "model_dump") and callable(value.model_dump):
            return safe_serialize(value.model_dump())
        if isinstance(value, dict):
            return {str(safe_serialize(key)): safe_serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [safe_serialize(item) for item in value]
        return {"type": type(value).__name__, "repr": repr(value)[:1000]}
    except Exception as exc:
        try:
            rendered = repr(value)[:1000]
        except Exception:
            rendered = "<unrepresentable>"
        return {
            "serialization_error": str(exc),
            "type": type(value).__name__,
            "repr": rendered,
        }
