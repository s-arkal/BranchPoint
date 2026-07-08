"""Provenance reference value objects."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .context import get_current_dependency_refs

from .serialization import safe_serialize


@dataclass(frozen=True)
class ProvenanceRef:
    event_id: str
    path: tuple[Any, ...] = ()
    source_event_type: str | None = None
    source_event_name: str | None = None
    reason: str = "unknown"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(
            (
                self.event_id,
                _stable_json(self.path),
                self.source_event_type,
                self.source_event_name,
                self.reason,
                self.confidence,
                _stable_json(self.metadata),
            )
        )


def ref_to_dict(ref: ProvenanceRef) -> dict[str, Any]:
    return {
        "event_id": ref.event_id,
        "path": safe_serialize(list(ref.path)),
        "source_event_type": ref.source_event_type,
        "source_event_name": ref.source_event_name,
        "reason": ref.reason,
        "confidence": ref.confidence,
        "metadata": safe_serialize(ref.metadata),
    }


def refs_to_dicts(refs: list[ProvenanceRef] | set[ProvenanceRef]) -> list[dict[str, Any]]:
    return [ref_to_dict(ref) for ref in sorted(refs, key=_sort_key)]


def normalize_input_refs(
    refs: Any,
    *,
    reason: str = "manual_input_ref",
) -> list[ProvenanceRef]:
    normalized: list[ProvenanceRef] = []
    for ref in _as_items(refs):
        if ref is None:
            continue
        if isinstance(ref, ProvenanceRef):
            normalized.append(ref)
            continue
        if isinstance(ref, str):
            normalized.append(ProvenanceRef(event_id=ref, reason=reason))
            continue
        if isinstance(ref, Mapping):
            event_id = ref.get("event_id")
            if isinstance(event_id, str):
                path = ref.get("path", ())
                if not isinstance(path, tuple):
                    path = tuple(path) if isinstance(path, list) else (path,)
                normalized.append(
                    ProvenanceRef(
                        event_id=event_id,
                        path=path,
                        source_event_type=_optional_str(ref.get("source_event_type")),
                        source_event_name=_optional_str(ref.get("source_event_name")),
                        reason=_optional_str(ref.get("reason")) or reason,
                        confidence=_float_or_default(ref.get("confidence"), 1.0),
                        metadata=dict(ref.get("metadata") or {}),
                    )
                )
                continue
        event_id = getattr(ref, "event_id", None)
        if isinstance(event_id, str):
            normalized.append(
                ProvenanceRef(
                    event_id=event_id,
                    source_event_type=_optional_str(getattr(ref, "type", None)),
                    source_event_name=_optional_str(getattr(ref, "name", None)),
                    reason=reason,
                )
            )
    return normalized


def collect_input_refs(
    tracker: Any,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    manual_input_refs: Any = None,
    depends_on_values: Any = None,
    include_context_refs: bool = True,
) -> tuple[list[ProvenanceRef], list[str]]:
    refs: list[ProvenanceRef] = []
    excluded_arg_indexes = set(exclude_args or [])
    excluded_kwarg_names = set(exclude_kwargs or [])

    for index, value in enumerate(args):
        if index not in excluded_arg_indexes:
            refs.extend(tracker.get_refs(value))
    for key, value in (kwargs or {}).items():
        if key not in excluded_kwarg_names:
            refs.extend(tracker.get_refs(value))
    for value in _as_items(depends_on_values):
        refs.extend(tracker.get_refs(value))
    refs.extend(normalize_input_refs(manual_input_refs, reason="manual_input_ref"))
    if include_context_refs:
        refs.extend(normalize_input_refs(get_current_dependency_refs(), reason="depends_on_context"))

    return _dedupe_refs(refs)


def _sort_key(ref: ProvenanceRef) -> tuple[str, str, str, str]:
    return (ref.event_id, _stable_json(ref.path), ref.reason, _stable_json(ref.metadata))


def _dedupe_refs(refs: Iterable[ProvenanceRef]) -> tuple[list[ProvenanceRef], list[str]]:
    seen_refs: set[ProvenanceRef] = set()
    unique_refs: list[ProvenanceRef] = []
    seen_event_ids: set[str] = set()
    event_ids: list[str] = []
    for ref in refs:
        if ref not in seen_refs:
            unique_refs.append(ref)
            seen_refs.add(ref)
        if ref.event_id not in seen_event_ids:
            event_ids.append(ref.event_id)
            seen_event_ids.add(ref.event_id)
    return unique_refs, event_ids


def _as_items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray, ProvenanceRef, Mapping)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _float_or_default(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _stable_json(value: Any) -> str:
    return json.dumps(safe_serialize(value), sort_keys=True)
