"""Provenance-preserving prompt helpers."""

from __future__ import annotations

import json
from typing import Any

from branchpoint.core.provenance import ProvenanceTracker
from branchpoint.core.refs import ProvenanceRef, normalize_input_refs
from branchpoint.core.serialization import safe_serialize


class BranchPointPrompt:
    def __init__(self, tracker: ProvenanceTracker) -> None:
        self.parts: list[str] = []
        self.refs: set[ProvenanceRef] = set()
        self.tracker = tracker

    def add(self, text: Any, ref: Any | None = None) -> "BranchPointPrompt":
        self.parts.append(str(text))
        if ref is None:
            self._collect_refs(text)
        else:
            self._collect_refs(ref, allow_manual_refs=True)
        return self

    def add_json(self, value: Any) -> "BranchPointPrompt":
        self.parts.append(json.dumps(safe_serialize(value), sort_keys=True))
        self._collect_refs(value)
        return self

    def __str__(self) -> str:
        return "".join(self.parts)

    def __repr__(self) -> str:
        return str(self)

    def __format__(self, format_spec: str) -> str:
        return format(str(self), format_spec)

    def __len__(self) -> int:
        return len(str(self))

    def __bool__(self) -> bool:
        return bool(str(self))

    def __branchpoint_refs__(self) -> tuple[ProvenanceRef, ...]:
        return tuple(sorted(self.refs, key=lambda ref: (ref.event_id, repr(ref.path), ref.reason)))

    def __branchpoint_serialize__(self) -> str:
        return str(self)

    def _collect_refs(self, value: Any, *, allow_manual_refs: bool = False) -> None:
        self.refs.update(self.tracker.get_refs(value))
        if allow_manual_refs:
            self.refs.update(normalize_input_refs(value, reason="prompt_ref"))
