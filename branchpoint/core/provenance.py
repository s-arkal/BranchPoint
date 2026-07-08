"""Sidecar provenance tracking."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from .refs import ProvenanceRef, normalize_input_refs, refs_to_dicts
from .schema import TraceEvent


class ProvenanceTracker:
    def __init__(self) -> None:
        self._by_object_id: dict[int, set[ProvenanceRef]] = {}
        self._object_type_by_id: dict[int, str] = {}

    def clear(self) -> None:
        self._by_object_id.clear()
        self._object_type_by_id.clear()

    def attach(
        self,
        value: Any,
        event: str | TraceEvent,
        *,
        path: tuple[Any, ...] = (),
        reason: str = "return_value",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        if not _is_trackable(value):
            return value
        ref = _make_ref(
            event,
            path=path,
            reason=reason,
            confidence=confidence,
            metadata=metadata,
        )
        object_id = id(value)
        self._by_object_id.setdefault(object_id, set()).add(ref)
        self._object_type_by_id[object_id] = type(value).__name__
        return value

    def get_refs(self, value: Any, recursive: bool = True) -> list[ProvenanceRef]:
        refs: set[ProvenanceRef] = set()
        self._collect_refs(value, refs, recursive=recursive, visited=set())
        return sorted(refs, key=lambda ref: (ref.event_id, repr(ref.path), ref.reason))

    def event_ids(self, value: Any, recursive: bool = True) -> list[str]:
        return sorted({ref.event_id for ref in self.get_refs(value, recursive=recursive)})

    def details(self, value: Any, recursive: bool = True) -> list[dict[str, Any]]:
        return refs_to_dicts(set(self.get_refs(value, recursive=recursive)))

    def _collect_refs(
        self,
        value: Any,
        refs: set[ProvenanceRef],
        *,
        recursive: bool,
        visited: set[int],
    ) -> None:
        branchpoint_refs = _branchpoint_refs(value)
        if branchpoint_refs is not None:
            refs.update(normalize_input_refs(branchpoint_refs, reason="branchpoint_refs"))
        if not _is_trackable(value):
            return
        object_id = id(value)
        if object_id in visited:
            return
        visited.add(object_id)
        refs.update(self._by_object_id.get(object_id, set()))
        if not recursive:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self._collect_refs(key, refs, recursive=True, visited=visited)
                self._collect_refs(item, refs, recursive=True, visited=visited)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                self._collect_refs(item, refs, recursive=True, visited=visited)
            return
        if is_dataclass(value) and not isinstance(value, type):
            for field in fields(value):
                self._collect_refs(getattr(value, field.name), refs, recursive=True, visited=visited)
            return
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                self._collect_refs(model_dump(), refs, recursive=True, visited=visited)
            except Exception:
                return


def _make_ref(
    event: str | TraceEvent,
    *,
    path: tuple[Any, ...],
    reason: str,
    confidence: float,
    metadata: dict[str, Any] | None,
) -> ProvenanceRef:
    if isinstance(event, TraceEvent):
        return ProvenanceRef(
            event_id=event.event_id,
            path=path,
            source_event_type=event.type,
            source_event_name=event.name,
            reason=reason,
            confidence=confidence,
            metadata=metadata or {},
        )
    return ProvenanceRef(
        event_id=event,
        path=path,
        reason=reason,
        confidence=confidence,
        metadata=metadata or {},
    )


def _branchpoint_refs(value: Any) -> Any | None:
    refs = getattr(value, "__branchpoint_refs__", None)
    if refs is None:
        return None
    return refs() if callable(refs) else refs


def _is_trackable(value: Any) -> bool:
    return value is not None and not isinstance(value, (str, int, float, bool, bytes, bytearray))
