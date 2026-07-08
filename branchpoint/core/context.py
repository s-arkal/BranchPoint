"""Context variables for active trace recording."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional

current_run_id: ContextVar[Optional[str]] = ContextVar("current_run_id", default=None)
current_project_id: ContextVar[Optional[str]] = ContextVar("current_project_id", default=None)
current_parent_event_id: ContextVar[Optional[str]] = ContextVar("current_parent_event_id", default=None)
current_span_id: ContextVar[Optional[str]] = ContextVar("current_span_id", default=None)
current_dependency_refs: ContextVar[tuple[Any, ...]] = ContextVar("current_dependency_refs", default=())


def get_current_run_id() -> Optional[str]:
    return current_run_id.get()


def get_current_project_id() -> Optional[str]:
    return current_project_id.get()


def get_current_parent_event_id() -> Optional[str]:
    return current_parent_event_id.get()


def get_current_span_id() -> Optional[str]:
    return current_span_id.get()


def get_current_dependency_refs() -> tuple[Any, ...]:
    return current_dependency_refs.get()


def set_trace_context(run_id: str, project_id: str) -> tuple[Token[Optional[str]], Token[Optional[str]]]:
    return current_run_id.set(run_id), current_project_id.set(project_id)


def reset_trace_context(tokens: tuple[Token[Optional[str]], Token[Optional[str]]]) -> None:
    run_token, project_token = tokens
    current_run_id.reset(run_token)
    current_project_id.reset(project_token)


def set_parent_event(event_id: Optional[str]) -> Token[Optional[str]]:
    return current_parent_event_id.set(event_id)


def reset_parent_event(token: Token[Optional[str]]) -> None:
    current_parent_event_id.reset(token)


def set_span(span_id: Optional[str]) -> Token[Optional[str]]:
    return current_span_id.set(span_id)


def reset_span(token: Token[Optional[str]]) -> None:
    current_span_id.reset(token)


def set_dependency_refs(refs: tuple[Any, ...]) -> Token[tuple[Any, ...]]:
    return current_dependency_refs.set(refs)


def reset_dependency_refs(token: Token[tuple[Any, ...]]) -> None:
    current_dependency_refs.reset(token)
