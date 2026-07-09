"""Framework-agnostic trace schema."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .errors import EventContractError

SCHEMA_VERSION = "v1"

USER_REQUEST = "userrequest"
SYSTEM_PROMPT = "systemprompt"
LLM_CALL = "llmcall"
LLM_OUTPUT = "llmoutput"
TOOL_CALL = "toolcall"
TOOL_OUTPUT = "tooloutput"
RETRIEVAL_QUERY = "retrievalquery"
RETRIEVAL_RESULT = "retrievalresult"
MEMORY_READ = "memoryread"
MEMORY_WRITE = "memorywrite"
STATE_READ = "state_read"
STATE_WRITE = "state_write"
ROUTING_DECISION = "routingdecision"
HANDOFF = "handoff"
VALIDATION_CHECK = "validationcheck"
RETRY = "retry"
FINAL_OUTPUT = "finaloutput"
FAILURE_LABEL = "failurelabel"
CUSTOM = "custom"

EVENT_TYPES = {
    USER_REQUEST,
    SYSTEM_PROMPT,
    LLM_CALL,
    LLM_OUTPUT,
    TOOL_CALL,
    TOOL_OUTPUT,
    RETRIEVAL_QUERY,
    RETRIEVAL_RESULT,
    MEMORY_READ,
    MEMORY_WRITE,
    STATE_READ,
    STATE_WRITE,
    ROUTING_DECISION,
    HANDOFF,
    VALIDATION_CHECK,
    RETRY,
    FINAL_OUTPUT,
    FAILURE_LABEL,
    CUSTOM,
}

SUCCESS = "success"
ERROR = "error"
TIMEOUT = "timeout"
SKIPPED = "skipped"
VALIDATION_FAILED = "validation_failed"
PARTIAL = "partial"
UNKNOWN = "unknown"
RUNNING = "running"
CANCELLED = "cancelled"

STATUS_VALUES = {
    SUCCESS,
    ERROR,
    TIMEOUT,
    SKIPPED,
    VALIDATION_FAILED,
    PARTIAL,
    UNKNOWN,
    RUNNING,
    CANCELLED,
}

METADATA_CUSTOM_TYPE = "custom_type"
METADATA_ERROR_TYPE = "error_type"
METADATA_ERROR_MESSAGE = "error_message"
METADATA_EXCEPTION_REPR = "exception_repr"
METADATA_MEMORY_KEY = "memory_key"
METADATA_OPERATION = "operation"
METADATA_PROVENANCE = "provenance"
METADATA_STATE_NAME = "state_name"
METADATA_STATE_PATH = "state_path"
METADATA_BEFORE_HASH = "before_hash"
METADATA_AFTER_HASH = "after_hash"
METADATA_VALUE_HASH = "value_hash"
METADATA_SNAPSHOT_IDS = "snapshot_ids"
METADATA_SNAPSHOTS = "snapshots"

STANDARD_METADATA_KEYS = {
    METADATA_CUSTOM_TYPE,
    METADATA_ERROR_TYPE,
    METADATA_ERROR_MESSAGE,
    METADATA_EXCEPTION_REPR,
    METADATA_MEMORY_KEY,
    METADATA_OPERATION,
    METADATA_PROVENANCE,
    METADATA_STATE_NAME,
    METADATA_STATE_PATH,
    METADATA_BEFORE_HASH,
    METADATA_AFTER_HASH,
    METADATA_VALUE_HASH,
    METADATA_SNAPSHOT_IDS,
    METADATA_SNAPSHOTS,
}

SNAPSHOT_MEMORY_BEFORE = "memory_before"
SNAPSHOT_MEMORY_AFTER = "memory_after"
SNAPSHOT_STATE_BEFORE = "state_before"
SNAPSHOT_STATE_AFTER = "state_after"
SNAPSHOT_STATE_DIFF = "state_diff"
SNAPSHOT_TOOL_OUTPUT = "tool_output"
SNAPSHOT_RETRIEVAL_RESULT = "retrieval_result"
SNAPSHOT_LLM_PROMPT = "llm_prompt"
SNAPSHOT_LLM_RESPONSE = "llm_response"
SNAPSHOT_VALIDATION_RESULT = "validation_result"
SNAPSHOT_CUSTOM = "custom"

SNAPSHOT_KINDS = {
    SNAPSHOT_MEMORY_BEFORE,
    SNAPSHOT_MEMORY_AFTER,
    SNAPSHOT_STATE_BEFORE,
    SNAPSHOT_STATE_AFTER,
    SNAPSHOT_STATE_DIFF,
    SNAPSHOT_TOOL_OUTPUT,
    SNAPSHOT_RETRIEVAL_RESULT,
    SNAPSHOT_LLM_PROMPT,
    SNAPSHOT_LLM_RESPONSE,
    SNAPSHOT_VALIDATION_RESULT,
    SNAPSHOT_CUSTOM,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_schema_version(schema_version: str | None) -> str:
    resolved_version = schema_version or SCHEMA_VERSION
    if resolved_version != SCHEMA_VERSION:
        raise EventContractError(f"Unsupported BranchPoint schema_version {resolved_version!r}; expected {SCHEMA_VERSION!r}")
    return resolved_version


def validate_status(status: str) -> None:
    if status not in STATUS_VALUES:
        allowed = ", ".join(sorted(STATUS_VALUES))
        raise EventContractError(f"Invalid BranchPoint status {status!r}; expected one of: {allowed}")


def validate_snapshot_kind(kind: str) -> None:
    if kind not in SNAPSHOT_KINDS:
        allowed = ", ".join(sorted(SNAPSHOT_KINDS))
        raise EventContractError(f"Invalid BranchPoint snapshot kind {kind!r}; expected one of: {allowed}")


def validate_event_type(event_type: str, metadata: dict[str, Any] | None = None, *, strict: bool = True) -> None:
    event_metadata = metadata or {}
    if event_type == CUSTOM:
        custom_type = event_metadata.get(METADATA_CUSTOM_TYPE)
        if strict and (not isinstance(custom_type, str) or not custom_type.strip()):
            raise EventContractError('Custom BranchPoint events require metadata["custom_type"] in strict mode')
        return

    if event_type in EVENT_TYPES:
        return

    if strict:
        allowed = ", ".join(sorted(EVENT_TYPES))
        raise EventContractError(f"Invalid BranchPoint event type {event_type!r}; expected one of: {allowed}")


def validate_event_contract(
    event_type: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    *,
    strict_event_types: bool = True,
    schema_version: str | None = SCHEMA_VERSION,
) -> None:
    validate_schema_version(schema_version)
    validate_event_type(event_type, metadata, strict=strict_event_types)
    validate_status(status)


def canonical_state_name(state_name: str | None) -> str:
    if state_name is None:
        return "default"
    if not isinstance(state_name, str) or not state_name.strip():
        raise EventContractError("BranchPoint state_name must be a non-empty string")
    return state_name.strip()


def canonical_state_path(path: str | Sequence[Any]) -> str:
    if isinstance(path, str):
        return _canonicalize_json_pointer(path)
    if isinstance(path, bytes | bytearray) or not isinstance(path, Sequence):
        raise EventContractError("BranchPoint state_path must be a JSON Pointer string or sequence of path segments")
    if not path:
        return ""
    return "/" + "/".join(_escape_json_pointer_segment(segment) for segment in path)


def state_path_contains(write_path: str, read_path: str) -> bool:
    canonical_write_path = canonical_state_path(write_path)
    canonical_read_path = canonical_state_path(read_path)
    return (
        canonical_write_path == canonical_read_path
        or canonical_write_path == ""
        or canonical_read_path.startswith(f"{canonical_write_path}/")
    )


def _canonicalize_json_pointer(path: str) -> str:
    if path == "":
        return ""
    if not path.startswith("/"):
        raise EventContractError("BranchPoint state_path must be a JSON Pointer string starting with '/'")
    return "/" + "/".join(_escape_json_pointer_segment(_unescape_json_pointer_segment(segment)) for segment in path[1:].split("/"))


def _escape_json_pointer_segment(segment: Any) -> str:
    if isinstance(segment, bool) or segment is None:
        raise EventContractError("BranchPoint state_path segments must be strings or integers")
    if not isinstance(segment, str | int):
        raise EventContractError("BranchPoint state_path segments must be strings or integers")
    return str(segment).replace("~", "~0").replace("/", "~1")


def _unescape_json_pointer_segment(segment: str) -> str:
    index = 0
    while index < len(segment):
        if segment[index] == "~":
            if index + 1 >= len(segment) or segment[index + 1] not in {"0", "1"}:
                raise EventContractError("BranchPoint state_path contains an invalid JSON Pointer '~' escape")
            index += 2
        else:
            index += 1
    return segment.replace("~1", "/").replace("~0", "~")


def error_metadata(exc: BaseException) -> dict[str, str]:
    return {
        METADATA_ERROR_TYPE: type(exc).__name__,
        METADATA_ERROR_MESSAGE: str(exc),
        METADATA_EXCEPTION_REPR: repr(exc),
    }


@dataclass
class TraceEvent:
    event_id: str
    run_id: str
    project_id: str

    type: str
    schema_version: str = SCHEMA_VERSION
    name: Optional[str] = None

    parent_id: Optional[str] = None
    span_id: Optional[str] = None

    timestamp_start: str = ""
    timestamp_end: Optional[str] = None

    input: Any = None
    output: Any = None

    input_refs: list[str] = field(default_factory=list)
    output_refs: list[str] = field(default_factory=list)

    status: str = SUCCESS

    metadata: dict[str, Any] = field(default_factory=dict)

    input_payload_ref: Optional[str] = None
    output_payload_ref: Optional[str] = None

    input_hash: Optional[str] = None
    output_hash: Optional[str] = None

    def __post_init__(self) -> None:
        self.schema_version = validate_schema_version(self.schema_version)
        if not self.timestamp_start:
            self.timestamp_start = utc_now_iso()


@dataclass
class TraceRun:
    run_id: str
    project_id: str
    name: Optional[str]

    started_at: str
    schema_version: str = SCHEMA_VERSION
    ended_at: Optional[str] = None

    status: str = RUNNING
    failure_label: Optional[str] = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.schema_version = validate_schema_version(self.schema_version)
        validate_status(self.status)


@dataclass
class Snapshot:
    snapshot_id: str
    run_id: str
    project_id: str
    kind: str
    event_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    name: Optional[str] = None
    timestamp: str = ""
    payload: Any = None
    payload_ref: Optional[str] = None
    payload_hash: Optional[str] = None
    preview: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.schema_version = validate_schema_version(self.schema_version)
        validate_snapshot_kind(self.kind)
        if not self.timestamp:
            self.timestamp = utc_now_iso()
