"""Snapshot helpers for payload hashing, previews, and state diffs."""

from __future__ import annotations

from typing import Any

from .errors import EventContractError
from .schema import Snapshot
from .serialization import (
    RedactionConfig,
    hash_serialized_payload,
    prepare_serialized_payload,
    preview_serialize,
    safe_serialize,
    safe_serialize_for_storage,
)

SNAPSHOT_PREVIEW_BYTES = 512


def hash_json(value: Any) -> str:
    return hash_serialized_payload(value)


def preview_json(value: Any, *, max_bytes: int = SNAPSHOT_PREVIEW_BYTES) -> Any:
    return preview_serialize(value, max_chars=max_bytes)


def prepare_snapshot_payload(
    snapshot: Snapshot,
    blob_store: Any,
    *,
    redaction_config: RedactionConfig | None = None,
    max_preview_chars: int = SNAPSHOT_PREVIEW_BYTES,
    max_blob_bytes: int | None = None,
) -> Snapshot:
    prepared = prepare_serialized_payload(
        snapshot.payload,
        redaction_config=redaction_config,
        max_preview_chars=max_preview_chars,
        max_blob_bytes=max_blob_bytes,
    )
    snapshot.payload_hash = prepared.payload_hash
    snapshot.preview = prepared.preview
    metadata_result = safe_serialize_for_storage(snapshot.metadata or {}, redaction_config=redaction_config)
    snapshot.metadata = _metadata_with_payload_safety(
        metadata_result.value,
        metadata_redaction=metadata_result.metadata() if metadata_result.redacted else None,
        redaction=prepared.redaction,
        truncation=prepared.truncation,
    )
    if blob_store.should_externalize(prepared.value):
        snapshot.payload_ref = blob_store.put_snapshot_json(snapshot.run_id, snapshot.snapshot_id, prepared.value)
        snapshot.payload = None
    else:
        snapshot.payload = prepared.value
    return snapshot


def verify_snapshot_payload(snapshot: Snapshot, payload: Any) -> Any:
    safe_payload = safe_serialize(payload)
    if snapshot.payload_hash is not None and hash_json(safe_payload) != snapshot.payload_hash:
        raise EventContractError(f"Snapshot payload hash mismatch for {snapshot.snapshot_id!r}")
    return safe_payload


def _metadata_with_payload_safety(
    metadata: dict[str, Any],
    *,
    metadata_redaction: dict[str, Any] | None,
    redaction: dict[str, Any],
    truncation: dict[str, Any],
) -> dict[str, Any]:
    safe_metadata = dict(safe_serialize(metadata or {}))
    if metadata_redaction is not None:
        safe_metadata["metadata_redaction"] = metadata_redaction
    if redaction.get("redacted"):
        safe_metadata["redaction"] = redaction
    if truncation.get("truncated"):
        safe_metadata["truncation"] = truncation
    return safe_metadata


def link_snapshot_metadata(metadata: dict[str, Any], snapshot: Snapshot) -> dict[str, Any]:
    event_metadata = dict(metadata)
    snapshot_ids = list(event_metadata.get("snapshot_ids") or [])
    if snapshot.snapshot_id not in snapshot_ids:
        snapshot_ids.append(snapshot.snapshot_id)
    event_metadata["snapshot_ids"] = snapshot_ids

    snapshots_by_kind = event_metadata.get("snapshots")
    if not isinstance(snapshots_by_kind, dict):
        snapshots_by_kind = {}
    kind_value = snapshots_by_kind.get(snapshot.kind)
    if kind_value is None:
        snapshots_by_kind[snapshot.kind] = snapshot.snapshot_id
    elif isinstance(kind_value, list):
        if snapshot.snapshot_id not in kind_value:
            kind_value.append(snapshot.snapshot_id)
    elif kind_value != snapshot.snapshot_id:
        snapshots_by_kind[snapshot.kind] = [kind_value, snapshot.snapshot_id]
    event_metadata["snapshots"] = snapshots_by_kind
    return event_metadata


def diff_json_like(before: Any, after: Any) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    _diff(safe_serialize(before), safe_serialize(after), "", operations)
    return operations


def _diff(before: Any, after: Any, path: str, operations: list[dict[str, Any]]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        before_keys = set(before)
        after_keys = set(after)
        for key in sorted(before_keys - after_keys):
            operations.append({"op": "remove", "path": _child_path(path, key)})
        for key in sorted(after_keys - before_keys):
            operations.append({"op": "add", "path": _child_path(path, key), "value": after[key]})
        for key in sorted(before_keys & after_keys):
            _diff(before[key], after[key], _child_path(path, key), operations)
        return

    if isinstance(before, list) and isinstance(after, list):
        shared_length = min(len(before), len(after))
        for index in range(shared_length):
            _diff(before[index], after[index], _child_path(path, index), operations)
        for index in range(len(before) - 1, len(after) - 1, -1):
            operations.append({"op": "remove", "path": _child_path(path, index)})
        for index in range(shared_length, len(after)):
            operations.append({"op": "add", "path": _child_path(path, index), "value": after[index]})
        return

    if before != after:
        operations.append({"op": "replace", "path": path, "value": after})


def _child_path(path: str, segment: object) -> str:
    rendered_segment = str(segment).replace("~", "~0").replace("/", "~1")
    if path == "":
        return f"/{rendered_segment}"
    return f"{path}/{rendered_segment}"
