"""Filesystem JSON blob storage for large payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from branchpoint.core.serialization import safe_serialize

MAX_INLINE_BYTES = 16_000


class BlobStore:
    def __init__(self, root: str | Path = ".branchpoint") -> None:
        self.root = Path(root)

    def put_json(self, run_id: str, event_id: str, kind: str, value: Any) -> str:
        safe_value = safe_serialize(value)
        directory = self.root / "runs" / run_id / "payloads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{event_id}_{kind}.json"
        path.write_text(json.dumps(safe_value, sort_keys=True), encoding="utf-8")
        return str(path.relative_to(self.root))

    def get_json(self, ref: str) -> Any:
        return json.loads((self.root / ref).read_text(encoding="utf-8"))

    def should_externalize(self, value: Any) -> bool:
        return len(json.dumps(safe_serialize(value), sort_keys=True).encode("utf-8")) > MAX_INLINE_BYTES
