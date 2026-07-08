"""Public BranchPoint client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.recorder import Recorder
from branchpoint.storage.blob_store import BlobStore
from branchpoint.storage.sqlite_store import SQLiteEventStore
from .decorators import (
    llm_decorator,
    memory_read_decorator,
    memory_write_decorator,
    retrieval_decorator,
    tool_decorator,
)


class BranchPoint:
    def __init__(self, project: str, db_path: str = ".branchpoint/branchpoint.sqlite") -> None:
        self.project_id = project
        self.db_path = db_path
        self.store = SQLiteEventStore(db_path=db_path)
        self.blob_store = BlobStore(Path(db_path).parent)
        self.recorder = Recorder(project_id=project, store=self.store, blob_store=self.blob_store)

    def trace(self, name: str | None = None, metadata: dict[str, Any] | None = None):
        return self.recorder.trace(name=name, metadata=metadata)

    def emit(self, *args: Any, **kwargs: Any):
        return self.recorder.emit(*args, **kwargs)

    def tool(self, name: str | None = None):
        return tool_decorator(self.recorder, name=name)

    def llm(self, name: str | None = None):
        return llm_decorator(self.recorder, name=name)

    def memory_read(self, name: str | None = None):
        return memory_read_decorator(self.recorder, name=name)

    def memory_write(self, name: str | None = None):
        return memory_write_decorator(self.recorder, name=name)

    def retrieval(self, name: str | None = None):
        return retrieval_decorator(self.recorder, name=name)

    def graph_builder(self) -> GraphBuilder:
        return GraphBuilder(self.store)
