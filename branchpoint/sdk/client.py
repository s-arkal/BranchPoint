"""Public BranchPoint client."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from branchpoint.core.context import reset_dependency_refs, set_dependency_refs
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.provenance import ProvenanceTracker
from branchpoint.core.recorder import Recorder
from branchpoint.core.refs import ProvenanceRef, collect_input_refs, refs_to_dicts
from branchpoint.storage.blob_store import BlobStore
from branchpoint.storage.sqlite_store import SQLiteEventStore
from .decorators import (
    llm_decorator,
    memory_read_decorator,
    memory_write_decorator,
    retrieval_decorator,
    tool_decorator,
)
from .prompt import BranchPointPrompt


class BranchPoint:
    def __init__(
        self,
        project: str,
        db_path: str = ".branchpoint/branchpoint.sqlite",
        *,
        provenance_mode: str = "hybrid",
    ) -> None:
        self.project_id = project
        self.db_path = db_path
        self.store = SQLiteEventStore(db_path=db_path)
        self.blob_store = BlobStore(Path(db_path).parent)
        self.provenance_tracker = ProvenanceTracker(provenance_mode=provenance_mode)
        self.recorder = Recorder(
            project_id=project,
            store=self.store,
            blob_store=self.blob_store,
            provenance_tracker=self.provenance_tracker,
        )

    def trace(self, name: str | None = None, metadata: dict[str, Any] | None = None):
        return self.recorder.trace(name=name, metadata=metadata)

    def emit(self, *args: Any, auto_refs: bool = True, **kwargs: Any):
        if not auto_refs:
            return self.recorder.emit(*args, **kwargs)

        bound = inspect.signature(self.recorder.emit).bind_partial(*args, **kwargs)
        arguments = bound.arguments
        input_args = (arguments["input"],) if "input" in arguments else ()
        detail_refs, input_refs = collect_input_refs(
            self.provenance_tracker,
            args=input_args,
            manual_input_refs=arguments.get("input_refs"),
            include_context_refs=True,
        )
        arguments["input_refs"] = input_refs
        arguments["metadata"] = _metadata_with_provenance(arguments.get("metadata"), detail_refs)
        return self.recorder.emit(**arguments)

    def depends_on(
        self,
        *values: Any,
        event_ids: list[str] | tuple[str, ...] | None = None,
        reason: str = "depends_on_context",
    ):
        return _DependencyContext(self.provenance_tracker, values, event_ids, reason)

    def prompt(self) -> BranchPointPrompt:
        return BranchPointPrompt(self.provenance_tracker)

    def format(self, template: str, **kwargs: Any) -> BranchPointPrompt:
        prompt = self.prompt()
        prompt.add(str(template).format(**kwargs), ref=tuple(kwargs.values()))
        return prompt

    def refs(self, *values: Any) -> list[str]:
        event_ids: set[str] = set()
        for value in values:
            event_ids.update(self.provenance_tracker.event_ids(value))
        return sorted(event_ids)

    def ref_details(self, *values: Any) -> list[dict[str, Any]]:
        details: dict[tuple[str, str, str], dict[str, Any]] = {}
        for value in values:
            for ref in self.provenance_tracker.details(value):
                key = (ref["event_id"], repr(ref["path"]), ref["reason"])
                details[key] = ref
        return [details[key] for key in sorted(details)]

    def unwrap(self, value: Any) -> Any:
        return self.provenance_tracker.unwrap(value)

    def detach(self, value: Any) -> Any:
        return self.provenance_tracker.detach(value)

    def tool(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return tool_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def llm(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return llm_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def memory_read(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return memory_read_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def memory_write(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return memory_write_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def retrieval(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return retrieval_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def graph_builder(self) -> GraphBuilder:
        return GraphBuilder(self.store)


class _DependencyContext:
    def __init__(
        self,
        tracker: ProvenanceTracker,
        values: tuple[Any, ...],
        event_ids: list[str] | tuple[str, ...] | None,
        reason: str,
    ) -> None:
        self.tracker = tracker
        self.values = values
        self.event_ids = event_ids
        self.reason = reason
        self._token = None

    def __enter__(self) -> "_DependencyContext":
        detail_refs, _ = collect_input_refs(
            self.tracker,
            depends_on_values=self.values,
            manual_input_refs=self.event_ids,
            include_context_refs=True,
        )
        detail_refs = [_replace_ref_reason(ref, self.reason) for ref in detail_refs]
        self._token = set_dependency_refs(tuple(detail_refs))
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if self._token is not None:
            reset_dependency_refs(self._token)
        return False


def _metadata_with_provenance(metadata: dict[str, Any] | None, input_refs: Any) -> dict[str, Any]:
    event_metadata = dict(metadata or {})
    existing_provenance = event_metadata.get("provenance")
    provenance = dict(existing_provenance) if isinstance(existing_provenance, dict) else {}
    provenance["input_refs_detail"] = refs_to_dicts(input_refs)
    event_metadata["provenance"] = provenance
    return event_metadata


def _replace_ref_reason(ref: ProvenanceRef, reason: str) -> ProvenanceRef:
    return ProvenanceRef(
        event_id=ref.event_id,
        path=ref.path,
        source_event_type=ref.source_event_type,
        source_event_name=ref.source_event_name,
        reason=reason,
        confidence=ref.confidence,
        metadata=ref.metadata,
    )
