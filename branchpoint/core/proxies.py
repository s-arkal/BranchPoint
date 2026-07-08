"""Provenance-preserving JSON-like proxy values."""

from __future__ import annotations

from typing import Any, Iterable, Iterator, TYPE_CHECKING

from .refs import ProvenanceRef, augment_ref_path, normalize_input_refs

if TYPE_CHECKING:
    from .provenance import ProvenanceTracker


class TrackedValue:
    def __init__(
        self,
        value: Any,
        refs: Iterable[ProvenanceRef],
        tracker: "ProvenanceTracker | None",
        path: tuple[Any, ...] = (),
        child_refs: Any = None,
    ) -> None:
        self._bp_value = value
        self._bp_refs = set(refs)
        self._bp_tracker = tracker
        self._bp_path = tuple(path)
        self._bp_generation = getattr(tracker, "generation", None)
        self._bp_child_refs = child_refs

    def __branchpoint_refs__(self) -> tuple[ProvenanceRef, ...]:
        return tuple(sorted(self._bp_active_refs(), key=lambda ref: (ref.event_id, repr(ref.path), ref.reason)))

    def _bp_active_refs(self) -> set[ProvenanceRef]:
        if self._bp_tracker is not None and not self._bp_tracker.is_generation_active(self._bp_generation):
            return set()
        return set(self._bp_refs)

    def __branchpoint_serialize__(self) -> Any:
        return unwrap(self)

    def unwrap(self) -> Any:
        return unwrap(self)


class TrackedDict(dict, TrackedValue):
    def __init__(
        self,
        value: dict[Any, Any],
        refs: Iterable[ProvenanceRef],
        tracker: "ProvenanceTracker | None",
        path: tuple[Any, ...] = (),
    ) -> None:
        dict.__init__(self)
        TrackedValue.__init__(self, self, refs, tracker, path, {})
        for key, item in value.items():
            self[key] = item

    def __getitem__(self, key: Any) -> Any:
        return self._bp_wrap_child(key, dict.__getitem__(self, key))

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return self[key]
        return default

    def items(self) -> Iterator[tuple[Any, Any]]:  # type: ignore[override]
        for key in dict.keys(self):
            yield key, self[key]

    def values(self) -> Iterator[Any]:  # type: ignore[override]
        for key in dict.keys(self):
            yield self[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._bp_child_refs[key] = set(_refs_from_value(value, self._bp_tracker))
        dict.__setitem__(self, key, unwrap(value))

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        other = dict(*args, **kwargs)
        for key, value in other.items():
            self[key] = value

    def setdefault(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
        if key not in self:
            self[key] = default
        return self[key]

    def _bp_wrap_child(self, key: Any, value: Any) -> Any:
        child_refs = set(self._bp_child_refs.get(key, set())) if self._bp_active_refs() else set()
        child_refs.update(augment_ref_path(ref, (key,), reason="field_read") for ref in self._bp_active_refs())
        return wrap_tracked(value, child_refs, self._bp_tracker, path=(*self._bp_path, key))


class TrackedList(list, TrackedValue):
    def __init__(
        self,
        value: list[Any],
        refs: Iterable[ProvenanceRef],
        tracker: "ProvenanceTracker | None",
        path: tuple[Any, ...] = (),
    ) -> None:
        list.__init__(self)
        TrackedValue.__init__(self, self, refs, tracker, path, [])
        for item in value:
            self.append(item)

    def __getitem__(self, index: Any) -> Any:
        item = list.__getitem__(self, index)
        if isinstance(index, slice):
            refs = [augment_ref_path(ref, (index,), reason="field_read") for ref in self._bp_active_refs()]
            return TrackedList(item, refs, self._bp_tracker, (*self._bp_path, index))
        return self._bp_wrap_child(index, item)

    def __iter__(self) -> Iterator[Any]:
        for index in range(len(self)):
            yield self[index]

    def append(self, value: Any) -> None:  # type: ignore[override]
        self._bp_child_refs.append(set(_refs_from_value(value, self._bp_tracker)))
        list.append(self, unwrap(value))

    def extend(self, values: Iterable[Any]) -> None:  # type: ignore[override]
        for value in values:
            self.append(value)

    def insert(self, index: int, value: Any) -> None:  # type: ignore[override]
        self._bp_child_refs.insert(index, set(_refs_from_value(value, self._bp_tracker)))
        list.insert(self, index, unwrap(value))

    def __setitem__(self, index: Any, value: Any) -> None:
        if isinstance(index, slice):
            values = list(value)
            self._bp_child_refs[index] = [set(_refs_from_value(item, self._bp_tracker)) for item in values]
            list.__setitem__(self, index, [unwrap(item) for item in values])
            return
        self._bp_child_refs[index] = set(_refs_from_value(value, self._bp_tracker))
        list.__setitem__(self, index, unwrap(value))

    def _bp_wrap_child(self, index: int, value: Any) -> Any:
        child_refs = set(self._bp_child_refs[index]) if self._bp_active_refs() and index < len(self._bp_child_refs) else set()
        child_refs.update(augment_ref_path(ref, (index,), reason="field_read") for ref in self._bp_active_refs())
        return wrap_tracked(value, child_refs, self._bp_tracker, path=(*self._bp_path, index))


class TrackedTuple(tuple):
    def __new__(
        cls,
        value: tuple[Any, ...],
        refs: Iterable[ProvenanceRef],
        tracker: "ProvenanceTracker | None",
        path: tuple[Any, ...] = (),
    ) -> "TrackedTuple":
        return tuple.__new__(cls, [unwrap(item) for item in value])

    def __init__(
        self,
        value: tuple[Any, ...],
        refs: Iterable[ProvenanceRef],
        tracker: "ProvenanceTracker | None",
        path: tuple[Any, ...] = (),
    ) -> None:
        self._bp_value = self
        self._bp_refs = set(refs)
        self._bp_tracker = tracker
        self._bp_path = tuple(path)
        self._bp_generation = getattr(tracker, "generation", None)
        self._bp_child_refs = [set(_refs_from_value(item, tracker)) for item in value]

    def __branchpoint_refs__(self) -> tuple[ProvenanceRef, ...]:
        return tuple(sorted(self._bp_active_refs(), key=lambda ref: (ref.event_id, repr(ref.path), ref.reason)))

    def _bp_active_refs(self) -> set[ProvenanceRef]:
        if self._bp_tracker is not None and not self._bp_tracker.is_generation_active(self._bp_generation):
            return set()
        return set(self._bp_refs)

    def __branchpoint_serialize__(self) -> Any:
        return unwrap(self)

    def unwrap(self) -> Any:
        return unwrap(self)

    def __getitem__(self, index: Any) -> Any:
        item = tuple.__getitem__(self, index)
        if isinstance(index, slice):
            refs = [augment_ref_path(ref, (index,), reason="field_read") for ref in self._bp_active_refs()]
            return TrackedTuple(item, refs, self._bp_tracker, (*self._bp_path, index))
        child_refs = set(self._bp_child_refs[index]) if self._bp_active_refs() else set()
        child_refs.update(augment_ref_path(ref, (index,), reason="field_read") for ref in self._bp_active_refs())
        return wrap_tracked(item, child_refs, self._bp_tracker, path=(*self._bp_path, index))

    def __iter__(self) -> Iterator[Any]:
        for index in range(len(self)):
            yield self[index]


class TrackedSet(set, TrackedValue):
    def __init__(
        self,
        value: set[Any],
        refs: Iterable[ProvenanceRef],
        tracker: "ProvenanceTracker | None",
        path: tuple[Any, ...] = (),
    ) -> None:
        set.__init__(self)
        TrackedValue.__init__(self, self, refs, tracker, path, {})
        for item in value:
            self.add(item)

    def __iter__(self) -> Iterator[Any]:
        for item in set.__iter__(self):
            child_refs = set(self._bp_child_refs.get(item, set())) if self._bp_active_refs() else set()
            child_refs.update(augment_ref_path(ref, (item,), reason="field_read") for ref in self._bp_active_refs())
            yield wrap_tracked(item, child_refs, self._bp_tracker, path=(*self._bp_path, item))

    def add(self, item: Any) -> None:  # type: ignore[override]
        plain_item = unwrap(item)
        self._bp_child_refs[plain_item] = set(_refs_from_value(item, self._bp_tracker))
        set.add(self, plain_item)

    def update(self, *others: Iterable[Any]) -> None:  # type: ignore[override]
        for other in others:
            for item in other:
                self.add(item)


class TrackedScalar(TrackedValue):
    def __bool__(self) -> bool:
        return bool(self._bp_value)

    def __eq__(self, other: Any) -> bool:
        return self._bp_value == unwrap(other)

    def __hash__(self) -> int:
        return hash(self._bp_value)

    def __repr__(self) -> str:
        return repr(self._bp_value)

    def __str__(self) -> str:
        return str(self._bp_value)

    def __int__(self) -> int:
        return int(self._bp_value)

    def __float__(self) -> float:
        return float(self._bp_value)

    def __format__(self, format_spec: str) -> str:
        return format(self._bp_value, format_spec)


class TrackedString(TrackedValue):
    def __contains__(self, item: Any) -> bool:
        return item in self._bp_value

    def __eq__(self, other: Any) -> bool:
        return self._bp_value == unwrap(other)

    def __hash__(self) -> int:
        return hash(self._bp_value)

    def __len__(self) -> int:
        return len(self._bp_value)

    def __repr__(self) -> str:
        return repr(self._bp_value)

    def __str__(self) -> str:
        return self._bp_value

    def __format__(self, format_spec: str) -> str:
        return format(self._bp_value, format_spec)

    def lower(self) -> "TrackedString":
        return TrackedString(self._bp_value.lower(), self.__branchpoint_refs__(), self._bp_tracker, self._bp_path)

    def upper(self) -> "TrackedString":
        return TrackedString(self._bp_value.upper(), self.__branchpoint_refs__(), self._bp_tracker, self._bp_path)

    def strip(self, chars: str | None = None) -> "TrackedString":
        return TrackedString(self._bp_value.strip(chars), self.__branchpoint_refs__(), self._bp_tracker, self._bp_path)

    def split(self, sep: str | None = None, maxsplit: int = -1) -> TrackedList:
        return TrackedList(self._bp_value.split(sep, maxsplit), self.__branchpoint_refs__(), self._bp_tracker, self._bp_path)


def wrap_tracked(
    value: Any,
    refs: Iterable[ProvenanceRef] | Any,
    tracker: "ProvenanceTracker | None",
    path: tuple[Any, ...] = (),
) -> Any:
    normalized_refs = set(normalize_input_refs(refs, reason="tracked_value"))
    if not normalized_refs:
        return value
    if isinstance(value, TrackedValue) or isinstance(value, TrackedTuple):
        existing_refs = set(value.__branchpoint_refs__())
        existing_refs.update(normalized_refs)
        return _rewrap(unwrap(value), existing_refs, tracker, path or getattr(value, "_bp_path", ()))
    if isinstance(value, dict):
        return TrackedDict(value, normalized_refs, tracker, path)
    if isinstance(value, list):
        return TrackedList(value, normalized_refs, tracker, path)
    if isinstance(value, tuple):
        return TrackedTuple(value, normalized_refs, tracker, path)
    if isinstance(value, set):
        return TrackedSet(value, normalized_refs, tracker, path)
    if isinstance(value, str):
        return TrackedString(value, normalized_refs, tracker, path)
    if value is None or isinstance(value, (bool, int, float)):
        return TrackedScalar(value, normalized_refs, tracker, path)
    return value


def unwrap(value: Any) -> Any:
    if isinstance(value, TrackedDict):
        return {unwrap(key): unwrap(item) for key, item in dict.items(value)}
    if isinstance(value, TrackedList):
        return [unwrap(item) for item in list.__iter__(value)]
    if isinstance(value, TrackedTuple):
        return tuple(unwrap(item) for item in tuple.__iter__(value))
    if isinstance(value, TrackedSet):
        return {unwrap(item) for item in set.__iter__(value)}
    if isinstance(value, TrackedValue):
        return unwrap(value._bp_value)
    branchpoint_serialize = getattr(value, "__branchpoint_serialize__", None)
    if callable(branchpoint_serialize):
        return branchpoint_serialize()
    return value


def detach(value: Any) -> Any:
    plain = unwrap(value)
    if isinstance(plain, dict):
        return {detach(key): detach(item) for key, item in plain.items()}
    if isinstance(plain, list):
        return [detach(item) for item in plain]
    if isinstance(plain, tuple):
        return tuple(detach(item) for item in plain)
    if isinstance(plain, set):
        return {detach(item) for item in plain}
    return plain


def _refs_from_value(value: Any, tracker: "ProvenanceTracker | None") -> tuple[ProvenanceRef, ...]:
    if tracker is not None:
        return tuple(tracker.get_refs(value))
    refs = getattr(value, "__branchpoint_refs__", None)
    if refs is None:
        return ()
    return tuple(normalize_input_refs(refs() if callable(refs) else refs, reason="branchpoint_refs"))


def _rewrap(
    value: Any,
    refs: Iterable[ProvenanceRef],
    tracker: "ProvenanceTracker | None",
    path: tuple[Any, ...],
) -> Any:
    if isinstance(value, dict):
        return TrackedDict(value, refs, tracker, path)
    if isinstance(value, list):
        return TrackedList(value, refs, tracker, path)
    if isinstance(value, tuple):
        return TrackedTuple(value, refs, tracker, path)
    if isinstance(value, set):
        return TrackedSet(value, refs, tracker, path)
    if isinstance(value, str):
        return TrackedString(value, refs, tracker, path)
    if value is None or isinstance(value, (bool, int, float)):
        return TrackedScalar(value, refs, tracker, path)
    return value
