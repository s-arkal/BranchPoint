"""Filesystem JSON blob storage for large payloads."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from branchpoint.core.errors import EventContractError
from branchpoint.core.serialization import canonical_serialize_for_hash, hash_serialized_payload, safe_serialize

MAX_INLINE_BYTES = 16_000


class BlobStore:
    def __init__(
        self,
        root: str | Path = ".branchpoint",
        *,
        max_inline_bytes: int = MAX_INLINE_BYTES,
        max_blob_bytes: int | None = None,
    ) -> None:
        self.root = Path(root)
        self.max_inline_bytes = max_inline_bytes
        self.max_blob_bytes = max_blob_bytes

    def put_json(self, run_id: str, event_id: str, kind: str, value: Any) -> str:
        safe_value = safe_serialize(value)
        directory = self.root / "runs" / run_id / "payloads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{event_id}_{kind}.json"
        self._write_json(path, safe_value)
        return str(path.relative_to(self.root))

    def put_snapshot_json(self, run_id: str, snapshot_id: str, value: Any) -> str:
        safe_value = safe_serialize(value)
        directory = self.root / "runs" / run_id / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{snapshot_id}.json"
        self._write_json(path, safe_value)
        return str(path.relative_to(self.root))

    def get_json(self, ref: str, *, expected_hash: str | None = None) -> Any:
        path = self.root / ref
        if not path.exists():
            raise EventContractError(f"BranchPoint blob is missing: {ref!r}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if expected_hash is not None:
            actual_hash = hash_serialized_payload(value)
            if actual_hash != expected_hash:
                raise EventContractError(f"BranchPoint blob hash mismatch for {ref!r}")
        return value

    def validate_json(self, ref: str, expected_hash: str | None = None) -> str | None:
        try:
            self.get_json(ref, expected_hash=expected_hash)
        except (EventContractError, OSError, json.JSONDecodeError) as exc:
            return str(exc)
        return None

    def cleanup_run(self, run_id: str) -> bool:
        path = self.root / "runs" / run_id
        if not path.exists():
            return False
        shutil.rmtree(path)
        return True

    def should_externalize(self, value: Any) -> bool:
        return len(canonical_serialize_for_hash(safe_serialize(value)).encode("utf-8")) > self.max_inline_bytes

    def _write_json(self, path: Path, value: Any) -> None:
        rendered = canonical_serialize_for_hash(value)
        if self.max_blob_bytes is not None and len(rendered.encode("utf-8")) > self.max_blob_bytes:
            raise EventContractError("BranchPoint blob exceeds max_blob_bytes after payload preparation")
        path.write_text(rendered, encoding="utf-8")
