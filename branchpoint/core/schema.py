"""Framework-agnostic trace schema."""

from __future__ import annotations

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

STANDARD_METADATA_KEYS = {
    METADATA_CUSTOM_TYPE,
    METADATA_ERROR_TYPE,
    METADATA_ERROR_MESSAGE,
    METADATA_EXCEPTION_REPR,
    METADATA_MEMORY_KEY,
    METADATA_OPERATION,
    METADATA_PROVENANCE,
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
