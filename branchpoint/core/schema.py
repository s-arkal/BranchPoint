"""Framework-agnostic trace schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

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

STATUS_VALUES = {
    SUCCESS,
    ERROR,
    TIMEOUT,
    SKIPPED,
    VALIDATION_FAILED,
    PARTIAL,
    UNKNOWN,
    RUNNING,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceEvent:
    event_id: str
    run_id: str
    project_id: str

    type: str
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
        if not self.timestamp_start:
            self.timestamp_start = utc_now_iso()


@dataclass
class TraceRun:
    run_id: str
    project_id: str
    name: Optional[str]

    started_at: str
    ended_at: Optional[str] = None

    status: str = RUNNING
    failure_label: Optional[str] = None

    metadata: dict[str, Any] = field(default_factory=dict)
