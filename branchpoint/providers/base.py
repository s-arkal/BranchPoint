"""Stable model-provider wrapper interfaces.

Provider wrappers are future integration points for clients such as hosted LLM
SDKs. This module defines shape and behavior guarantees only; it intentionally
does not import or implement any provider SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from branchpoint.core.schema import TraceEvent

ClientT = TypeVar("ClientT")

PROVIDER_WRAPPER_RULES = (
    "wrap(client) must preserve normal client return values and exceptions.",
    "Provider SDK imports must remain optional and outside BranchPoint core dependencies.",
    "Requests should record canonical llmcall events when a BranchPoint run is active.",
    "Responses should record canonical llmoutput events and attach provenance refs when available.",
    "Provider-specific request and response fields belong in metadata, snapshots, or payload refs.",
    "Wrappers must not implement scoring, ranking, replay, or provider cache acceleration.",
)


@dataclass(frozen=True)
class ProviderCallContext:
    """Framework-neutral metadata for one provider call."""

    provider: str
    operation: str
    model: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ModelProviderWrapper(Protocol[ClientT]):
    """Protocol for dependency-free model-provider wrappers.

    Implementations can wrap concrete provider clients in separate optional
    packages. The wrapper contract is intentionally small so future provider
    integrations can preserve native behavior while recording BranchPoint
    request and response events.
    """

    provider_name: str

    def wrap(self, client: ClientT) -> ClientT:
        """Return a client-compatible object without changing normal behavior."""

    def record_request(
        self,
        request: Any,
        *,
        context: ProviderCallContext | None = None,
    ) -> TraceEvent | None:
        """Record provider request details as a BranchPoint llmcall event."""

    def record_response(
        self,
        response: Any,
        *,
        request_event: TraceEvent | None = None,
        context: ProviderCallContext | None = None,
    ) -> TraceEvent | None:
        """Record provider response details as a BranchPoint llmoutput event."""


__all__ = ["ModelProviderWrapper", "PROVIDER_WRAPPER_RULES", "ProviderCallContext"]
